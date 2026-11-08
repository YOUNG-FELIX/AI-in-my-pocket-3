"""AI agent layer for the Pocket 3 tool server.

A cloud LLM (any OpenAI-compatible endpoint) is given the camera's tool
registry as function-calling tools and drives the hardware itself.

    user text/voice -> LLM -> tool_calls -> /api/call -> gimbal moves
                          -> final text  -> TTS + on-screen subtitle

Providers that are reachable from this machine (measured 2026-11-08):
    deepseek  api.deepseek.com       401 (reachable, needs key)
    moonshot  api.moonshot.cn       200
    zhipu     open.bigmodel.cn      200
    dashscope dashscope.aliyuncs.com 404 (reachable)
    openai    api.openai.com        000 UNREACHABLE from here

The key never goes in the source: pass --key / P3_LLM_KEY, or set it at
runtime through POST /api/agent/key.
"""
import json
import os
import urllib.parse
import urllib.request

PROVIDERS = {
    # name: (base_url, default_model, note)
    'deepseek': ('https://api.deepseek.com/v1', 'deepseek-chat'),
    'moonshot': ('https://api.moonshot.cn/v1', 'moonshot-v1-8k'),
    'zhipu': ('https://open.bigmodel.cn/api/paas/v4', 'glm-4-flash'),
    'dashscope': ('https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus'),
    'openai': ('https://api.openai.com/v1', 'gpt-4o-mini'),
    'mock': ('mock://local', 'mock-1'),
}

# The system prompt is deliberately concrete about the hardware limits,
# because a model that has not read the manuals will otherwise invent
# capabilities (recording start/stop is DUML-only, exposure is fixed, etc.).
SYSTEM = """你是一台 DJI Osmo Pocket 3 云台相机的"大脑"。你能通过工具真实地转动云台、变焦、拍照、录像。

硬性事实（不要幻想其它能力）：
- 云台 pan 范围 -35..215（正数向右转），tilt -90..90（正数=镜头向下），zoom 100..400（100=1x，400=4x）。
- 曝光/快门/ISO/白平衡/对焦 都**不可调**，硬件不暴露。不要答应调整它们。
- 你的人脸追踪是软件实现（track 工具），不是原厂 ActiveTrack，但能用。
- 用户看不到你的字，除非你调用 say（语音）或 caption（在视频画面上打出字幕）。

行为准则：
- 想看清某处就 look/look_rel 转过去，再 see 看一眼确认。
- **see 会把画面直接给你看**。用户问"你看到什么/前面是什么/我穿什么颜色"，就调 see，然后描述画面里真实出现的东西。
- 描述画面要具体（物体、颜色、位置、光线），但只说看得见的，不要脑补看不清的细节。
- 回复要短，一两句口语，像人在说话。不要罗列工具名。
- 谈吐自然，不要念 JSON。"""


def _schema(params):
    """Turn the server's terse param dict into JSON Schema properties."""
    props, req = {}, []
    for name, spec in (params or {}).items():
        spec = str(spec)
        opt = spec.endswith('?')
        base = spec.rstrip('?').lower()
        t = {'float': 'number', 'int': 'integer', 'bool': 'boolean'}.get(base, 'string')
        props[name] = {'type': t}
        if not opt:
            req.append(name)
    return props, req


class Agent:
    def __init__(self, provider='deepseek', key=None, model=None,
                 base=None, server='http://127.0.0.1:8770', api_key=None,
                 timeout=90, vision=True):
        self.provider = provider
        self.base = base or PROVIDERS.get(provider, PROVIDERS['deepseek'])[0]
        self.model = model or PROVIDERS.get(provider, ('', 'deepseek-chat'))[1]
        self.key = key or os.environ.get('P3_LLM_KEY') or ''
        self.server = server.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        # vision=False keeps tool results as plain JSON for text-only models.
        self.vision = vision
        self._tools = None
        self.history = []
        self.last_actions = []

    # -- plumbing --------------------------------------------------------
    def _url(self, path):
        u = self.server + path
        if self.api_key:
            u += ('&' if '?' in u else '?') + 'key=' + urllib.parse.quote(self.api_key)
        return u

    def _get(self, path, timeout=180):
        with urllib.request.urlopen(self._url(path), timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))

    def _post(self, path, obj, timeout=180):
        req = urllib.request.Request(
            self._url(path), data=json.dumps(obj).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))

    def tools(self):
        """Fetch the live tool registry and convert it to function schemas."""
        if self._tools is not None:
            return self._tools
        reg = self._get('/api/tools')
        out = []
        for t in reg['tools']:
            props, req = _schema(t['params'])
            out.append({'type': 'function', 'function': {
                'name': t['name'],
                'description': t['doc'],
                'parameters': {'type': 'object', 'properties': props,
                               'required': req}}})
        self._tools = out
        return out

    def call(self, name, args):
        q = urllib.parse.urlencode({k: v for k, v in (args or {}).items()})
        return self._get('/api/call?name=%s%s' % (
            urllib.parse.quote(name), ('&' + q) if q else ''))

    # -- vision ---------------------------------------------------------
    def _vision_part(self, path):
        """Turn a captured frame into an OpenAI-format image_url part.

        A frame is 1280x720 JPEG, which is far more tokens than the model
        needs to answer "what is in front of me". We downscale to <=768 px on
        the long edge and re-encode at quality 72, which keeps the picture
        readable for a vision model while costing roughly a fifth of the tokens
        of the raw capture.

        Returns None if the file cannot be read -- a missing frame must not
        break the turn, it just means the model answers from text alone.
        """
        if not self.vision or not path:
            return None
        try:
            import base64
            with open(path, 'rb') as f:
                raw = f.read()
            try:
                from PIL import Image
                import io as _io
                im = Image.open(_io.BytesIO(raw)).convert('RGB')
                w, h = im.size
                scale = 768.0 / max(w, h)
                if scale < 1.0:
                    im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                   Image.LANCZOS)
                buf = _io.BytesIO()
                im.save(buf, format='JPEG', quality=72)
                raw = buf.getvalue()
            except Exception:
                pass  # PIL optional; fall back to the original bytes
            return {'type': 'image_url',
                    'image_url': {'url': 'data:image/jpeg;base64,'
                                         + base64.b64encode(raw).decode('ascii')}}
        except Exception:
            return None

    def _tool_message(self, call_id, res):
        """Build the tool-result message, attaching a frame when the tool saw
        something. Text-only providers get the JSON as plain text; multimodal
        providers get JSON plus the picture so the model can reason about what
        is actually in front of the camera."""
        text = json.dumps(res, ensure_ascii=False)[:1800]
        if not isinstance(res, dict):
            return {'role': 'tool', 'tool_call_id': call_id, 'content': text}
        path = res.get('path')
        if not path:
            return {'role': 'tool', 'tool_call_id': call_id, 'content': text}
        part = self._vision_part(path)
        if part is None:
            return {'role': 'tool', 'tool_call_id': call_id, 'content': text}
        return {'role': 'tool', 'tool_call_id': call_id,
                'content': [{'type': 'text',
                             'text': 'Captured frame. Metadata: ' + text},
                            part]}

    # -- the model call --------------------------------------------------
    def _complete(self, messages, use_tools=True):
        body = {'model': self.model, 'messages': messages,
                'temperature': 0.6, 'max_tokens': 700}
        if use_tools:
            body['tools'] = self.tools()
            body['tool_choice'] = 'auto'
        req = urllib.request.Request(
            self.base.rstrip('/') + '/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.key})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        return data['choices'][0]['message']

    def _mock(self, messages):
        """Deterministic stand-in so the whole loop can be tested with no key."""
        last = ''
        for m in reversed(messages):
            if m.get('role') == 'user':
                last = m.get('content') or ''
                break
        n = sum(1 for m in messages if m.get('role') == 'tool')
        if n == 0:
            if '左' in last:
                args = {'dpan': -30}
            elif '右' in last:
                args = {'dpan': 30}
            elif '上' in last:
                args = {'dtilt': -15}
            elif '下' in last:
                args = {'dtilt': 15}
            elif '近' in last or '放大' in last:
                args = {'dzoom': 60}
            else:
                args = {'pan': 0, 'tilt': 0, 'zoom': 100}
            return {'role': 'assistant', 'content': '',
                    'tool_calls': [{'id': 'mock1', 'type': 'function',
                                    'function': {'name': 'look_rel',
                                                 'arguments': json.dumps(args)}}]}
        return {'role': 'assistant', 'content': '好，转过去了（mock 模式，没接真模型）。'}

    # -- public ----------------------------------------------------------
    @property
    def ready(self):
        return bool(self.key) or self.provider == 'mock'

    def chat(self, text, speak=True, caption=True, max_rounds=4):
        """One conversational turn. Returns dict with reply + what it did."""
        if not self.ready:
            return {'ok': False, 'error': 'no_api_key',
                    'hint': 'POST /api/agent/key  {"provider":"deepseek","key":"sk-..."}'
                            '  or start the server with --llm-key',
                    'reply': '我还没接上大模型，先给我一个 API key 吧。'}
        msgs = [{'role': 'system', 'content': SYSTEM}]
        msgs += self.history[-8:]
        msgs.append({'role': 'user', 'content': text})

        actions = []
        reply = ''
        for _ in range(max_rounds):
            m = self._mock(msgs) if self.provider == 'mock' else self._complete(msgs)
            calls = m.get('tool_calls') or []
            if not calls:
                reply = (m.get('content') or '').strip()
                break
            msgs.append({'role': 'assistant', 'content': m.get('content') or '',
                         'tool_calls': calls})
            for c in calls:
                fn = c['function']
                name = fn['name']
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except ValueError:
                    args = {}
                try:
                    res = self.call(name, args)
                    ok = True
                except Exception as e:
                    res, ok = {'error': '%s: %s' % (type(e).__name__, e)}, False
                actions.append({'tool': name, 'args': args, 'ok': ok,
                                'result': _trim(res)})
                msgs.append(self._tool_message(c.get('id', name), res))
        if not reply:
            reply = '（动完了，但这次没生成回复。）'

        self.history += [{'role': 'user', 'content': text},
                         {'role': 'assistant', 'content': reply}]
        self.history = self.history[-20:]
        self.last_actions = actions

        if reply and speak:
            try:
                self._get('/api/call?name=say&text=' + urllib.parse.quote(reply[:300]))
            except Exception:
                pass
        if reply and caption:
            try:
                self._get('/api/call?name=caption&text='
                          + urllib.parse.quote(reply[:120]) + '&seconds=8')
            except Exception:
                pass
        return {'ok': True, 'reply': reply, 'actions': actions,
                'provider': self.provider, 'model': self.model}


def _trim(res, limit=400):
    s = json.dumps(res, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + '...'


# ---------------------------------------------------------------------------
# CLI: quick text or voice turn against a running server
# ---------------------------------------------------------------------------
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('text', nargs='*')
    ap.add_argument('--provider', default='deepseek')
    ap.add_argument('--key', default=None)
    ap.add_argument('--model', default=None)
    ap.add_argument('--base', default=None)
    ap.add_argument('--server', default='http://127.0.0.1:8770')
    ap.add_argument('--api-key', default=None,
                      help='LLM provider api key. Omit and the agent runs in mock mode.')
    ap.add_argument('--voice', action='store_true', help='record then transcribe first')
    ap.add_argument('--seconds', type=float, default=4.0)
    ap.add_argument('--no-speak', action='store_true')
    a = ap.parse_args()

    ag = Agent(a.provider, a.key, a.model, a.base, a.server, a.api_key)
    text = ' '.join(a.text).strip()
    if a.voice or not text:
        print('listening %.1fs ...' % a.seconds, flush=True)
        r = ag._get('/api/call?name=listen&seconds=%s' % a.seconds)
        text = (r.get('text') or '').strip()
        print('heard: %r' % text, flush=True)
        if not text:
            print('nothing heard')
            return
    out = ag.chat(text, speak=not a.no_speak)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
