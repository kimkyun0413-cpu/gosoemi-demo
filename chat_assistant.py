"""AI 대화(글·음성) 규칙과 도우미. Streamlit에 의존하지 않는다.

- 대화 기록은 서버 메모리(이 브라우저 탭의 세션)에만 두고 DB에 저장하지 않는다.
- 사건 정보 정리는 이용자가 쓴 말만 근거로 한다. AI 답변에 나온 예시나 추측은 사실로 쓰지 않는다.
- 음성 대화는 브라우저 부품(VOICE_CHAT_JS)이 마이크를 듣다가 말이 끝나면 그 구간만 WAV로 보내고,
  서버가 기존 Gemini 받아쓰기·답변으로 처리한 뒤 답변을 기기 음성으로 읽는다. 읽는 동안에는 마이크 입력을 끈다.
- 답변 읽기는 브라우저가 '기기 안의 음성(localService)'이라고 알려 주는 한국어 음성만 쓴다.
  원격 음성(예: 일부 데스크톱 Chrome의 'Google 한국의')은 답변 글이 외부 음성 서버로 갈 수 있어 쓰지 않는다.
"""

import base64
import hashlib
import re
import time

CHAT_MAX_INPUT_CHARS = 3000
# 다음 질문에 함께 보낼 이전 대화 수(질문+답변 각각 1개). 비용과 응답 시간을 위해 최근 것만 보낸다.
CHAT_HISTORY_MESSAGES = 20
# 화면에 남겨 두는 대화 수. 넘으면 오래된 것부터 지운다.
CHAT_MAX_MESSAGES = 60
CHAT_DUPLICATE_WINDOW_SECONDS = 10
# 사건 정보 정리에 보내는 이용자 발언 글자 수 상한
CHAT_EXTRACT_MAX_CHARS = 8000
# 한 번에 읽어 주는 답변 글자 수 상한(기기 음성이 긴 글에서 멈추는 경우가 있어 문장 단위로 나눠 읽는다)
SPEECH_MAX_CHARS = 2000

CHAT_STYLE_INSTRUCTION = (
    "- 쉽고 자연스러운 존댓말로, 실제 상담원이 말하듯 대화하라.\n"
    "- 처음부터 긴 설명을 쏟아내지 마라. 한 번의 답변은 보통 3~6문장으로 짧게 하고, 목록은 꼭 필요할 때만 3개 이내로 써라.\n"
    "- 상황을 이해하는 데 필요한 정보가 부족하면 한 번에 하나만 물어보라(예: 언제 있었는지, 돈을 보냈는지, 남아 있는 자료가 있는지).\n"
    "- 같은 대화에서 이용자가 앞서 말한 내용을 기억하고, 이미 들은 것은 다시 묻지 마라.\n"
    "- 이용자가 말한 사실과 너의 추측·일반론을 구분하라. 추측은 '~일 수 있습니다', '일반적으로는'처럼 표시하라.\n"
    "- 범죄가 된다, 이긴다, 처벌된다, 돈을 돌려받는다고 단정하지 마라. 판단은 수사기관·법원의 몫이라고 필요할 때 짧게 알려라.\n"
    "- 확인하지 않은 판례, 법조문 번호, 고소 기한·공소시효 같은 기한을 만들어 말하지 마라. 필요하면 공식 기관이나 변호사에게 확인하라고 안내하라.\n"
    "- 너는 인터넷 검색을 하지 않는다. '최신 정보를 확인했다', '방금 찾아보니' 같은 표현을 쓰지 마라. 법·제도는 바뀔 수 있다고 필요할 때 알려라.\n"
    "- 이용자가 지금 위험하거나 협박·폭력이 이어지고 있다면 다른 안내보다 먼저 112 신고와 안전 확보를 안내하라.\n"
    "- 주민등록번호, 계좌 비밀번호, 카드번호, 인증번호(OTP)는 말하지 말라고 안내하고, 묻지도 마라.\n"
    "- 상황이 어느 정도 정리되면 '이 대화로 사건 정보 정리하기' 버튼으로 고소장 작성 화면에 옮길 수 있다고 한 번 알려 줘도 된다."
)

CHAT_WELCOME = (
    "안녕하세요. 어떤 일이 있으셨는지 편하게 말씀해 주세요. "
    "필요한 내용은 제가 하나씩 여쭤볼게요."
)

CHAT_LIMITS_NOTE = (
    "AI 답변은 일반 정보이며 법률 자문이 아닙니다. 인터넷 검색을 하지 않아 최신 법·제도와 다를 수 있고, "
    "범죄가 되는지나 결과는 수사기관·법원이 판단합니다."
)

CHAT_RETENTION_NOTE = (
    "대화 내용은 데이터베이스에 저장하지 않고 서버 메모리에만 잠시 둡니다. '새 대화'·'대화 지우기'를 누르거나 "
    "로그아웃·다른 계정으로 바꾸면 바로 지웁니다. 새로고침하거나 탭을 닫으면 화면에서 바로 사라지고, "
    "서버 메모리에서는 연결이 끊긴 뒤 약 2분이 지나면 정리됩니다. 답변을 만들기 위해 Google Gemini로 보낸 내용은 "
    "Google이 오남용 방지를 위해 일정 기간 보관할 수 있으며, 이 서비스가 지울 수 없습니다."
)

SPEECH_NOTE = (
    "답변은 이 기기(브라우저·운영체제)에 들어 있는 한국어 음성으로 기기 안에서 읽어 드립니다. "
    "답변 글을 외부 음성 서비스로 보내지 않으며, 기기에 한국어 음성이 없으면 글로만 보여 드립니다."
)

VOICE_MIC_NOTE = (
    "마이크는 이 화면에서 큰 마이크 버튼을 누른 뒤에만 켜지고, '마이크 끄기'·'대화 종료'를 누르거나 화면을 떠나면 꺼집니다. "
    "AI가 답하는 동안에는 마이크 입력을 끕니다. 말이 끝나면 그 부분의 녹음만 서버로 보내 Google Gemini로 글로 바꾸고, "
    "서버에서는 글로 바꾸는 동안만 메모리에 두며 저장하지 않습니다."
)

VOICE_STYLE_INSTRUCTION = (
    "- 지금은 음성으로 대화 중이다. 답변은 소리로 듣기 좋게 2~4문장으로 짧게 하고, 목록·표·기호·링크·영문 약어를 쓰지 마라.\n"
    "- 받아쓰기라 단어가 틀렸을 수 있다. 뜻이 분명하지 않으면 추측하지 말고 짧게 다시 물어보라."
)

# 음성 대화에서 한 번에 받는 말의 길이 상한(초). 브라우저 부품도 이 길이에서 끊어 보낸다.
VOICE_TURN_MAX_SECONDS = 40
# 16kHz 16비트 모노 WAV 기준 40초 ≈ 1.3MB. 여유를 두고 2MB까지만 받는다.
VOICE_TURN_MAX_BYTES = 2 * 1024 * 1024
VOICE_TURN_MIN_BYTES = 1024


def voice_system_instruction(service_identity: str, safety: str) -> str:
    return "\n\n".join([system_instruction(service_identity, safety), VOICE_STYLE_INSTRUCTION])


def decode_voice_turn(payload) -> tuple:
    """브라우저 부품이 보낸 말 한 마디 {sid, seq, audio(base64 WAV)}를 검사해 (sid, seq, wav bytes)로 돌려준다.

    형식이 틀리거나 너무 크고 작으면 ValueError. 이 값은 이용자 브라우저가 보낸 것이므로 모두 검사한다.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload")
    sid, seq, audio = payload.get("sid"), payload.get("seq"), payload.get("audio")
    if not (isinstance(sid, str) and re.fullmatch(r"[a-z0-9]{4,32}", sid)):
        raise ValueError("sid")
    if not (isinstance(seq, int) and 0 < seq < 1_000_000):
        raise ValueError("seq")
    if not isinstance(audio, str) or len(audio) > VOICE_TURN_MAX_BYTES * 4 // 3 + 8:
        raise ValueError("size")
    try:
        data = base64.b64decode(audio, validate=True)
    except Exception as error:
        raise ValueError("base64") from error
    if not (VOICE_TURN_MIN_BYTES <= len(data) <= VOICE_TURN_MAX_BYTES):
        raise ValueError("size")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("wav")
    return sid, seq, data



def system_instruction(service_identity: str, safety: str) -> str:
    return "\n\n".join([service_identity, safety, CHAT_STYLE_INSTRUCTION])


def model_contents(messages: list, question: str) -> list:
    """Gemini에 보낼 대화: 최근 대화 + 새 질문. 실패해 답이 없는 질문은 messages에 없으므로 섞이지 않는다."""
    history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages[-CHAT_HISTORY_MESSAGES:]
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    # Gemini는 대화가 이용자 차례로 시작해야 한다.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history + [{"role": "user", "parts": [{"text": question}]}]


def append_exchange(messages: list, question: str, answer: str, via_voice: bool = False) -> None:
    messages.append({"role": "user", "content": question, "voice": via_voice})
    messages.append({"role": "assistant", "content": answer})
    if len(messages) > CHAT_MAX_MESSAGES:
        del messages[: len(messages) - CHAT_MAX_MESSAGES]


def normalize_question(text: str) -> str:
    return (text or "").strip()[:CHAT_MAX_INPUT_CHARS]


def question_digest(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode("utf-8")).hexdigest()


def is_duplicate_submit(last: dict, question: str, now: float = None) -> bool:
    """방금(10초 안) 성공적으로 보낸 것과 같은 질문을 다시 보내면 True. 두 번 누르기·재실행 중복을 막는다."""
    if not last:
        return False
    now = time.time() if now is None else now
    return last.get("digest") == question_digest(question) and now - last.get("at", 0) < CHAT_DUPLICATE_WINDOW_SECONDS


def user_statements(messages: list) -> str:
    """사건 정보 정리에 쓰는 글: 이용자가 직접 쓰거나 말한 내용만, 말한 순서대로."""
    parts = [m["content"].strip() for m in messages if m.get("role") == "user" and (m.get("content") or "").strip()]
    text = "\n".join(parts)
    # 너무 길면 뒤쪽(최근) 내용을 남긴다.
    return text[-CHAT_EXTRACT_MAX_CHARS:]


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://)?[^)]+\)")
_URL_RE = re.compile(r"https?://\S+")


def speech_text(answer: str) -> str:
    """답변을 소리 내어 읽기 좋게 바꾼다: 마크다운 기호와 주소를 빼고 길이를 줄인다."""
    text = _MD_LINK_RE.sub(r"\1", answer or "")
    text = _URL_RE.sub("링크", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) > SPEECH_MAX_CHARS:
        cut = text[:SPEECH_MAX_CHARS]
        end = max(cut.rfind("."), cut.rfind("요"), cut.rfind("다"))
        text = (cut[: end + 1] if end > SPEECH_MAX_CHARS // 2 else cut) + " 나머지는 화면의 글로 확인해 주세요."
    return text




# ── 음성 대화 브라우저 부품 ─────────────────────────────────────────
# 한 번 화면에 올라오면(마운트) 상태를 parentElement에 두고, 새 data가 올 때마다 같은 상태에서 이어 간다.
# Streamlit은 data가 바뀔 때 이 함수를 다시 부르고, 화면에서 사라질 때만 마지막 정리 함수를 부른다.
# 마이크 권한은 큰 마이크 버튼을 눌렀을 때만 요청한다. 답변을 읽는 동안에는 마이크 트랙을 끈다(AI 목소리 재입력 방지).
VOICE_CHAT_CSS = """
.gv { display:flex; flex-direction:column; align-items:center; gap:14px; padding:18px 8px 6px; font-family:inherit; color:#0B1F3A; }
.gv-mic { width:112px; height:112px; border-radius:50%; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center;
  background:#C9D3E3; color:#fff; box-shadow:0 6px 18px rgba(11,31,58,.18); transition:background .2s, transform .1s; -webkit-tap-highlight-color:transparent; }
.gv-mic:active { transform:scale(.96); }
.gv-mic svg { width:46px; height:46px; }
.gv[data-phase="listening"] .gv-mic { background:#1F9D55; animation:gvpulse 1.6s infinite; }
.gv[data-phase="hearing"] .gv-mic { background:#14803F; animation:gvpulse .8s infinite; }
.gv[data-phase="thinking"] .gv-mic { background:#E0A100; }
.gv[data-phase="speaking"] .gv-mic { background:#2F80ED; }
.gv[data-phase="starting"] .gv-mic { background:#8FA3C0; }
@keyframes gvpulse { 0% { box-shadow:0 0 0 0 rgba(31,157,85,.45); } 70% { box-shadow:0 0 0 22px rgba(31,157,85,0); } 100% { box-shadow:0 0 0 0 rgba(31,157,85,0); } }
.gv-status { font-size:22px; font-weight:700; text-align:center; min-height:30px; }
.gv-hint { font-size:15px; color:#4A5A75; text-align:center; max-width:460px; line-height:1.5; min-height:22px; }
.gv-hint.warn { color:#B42318; }
.gv-row { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; width:100%; }
.gv-btn { flex:1 1 110px; max-width:160px; min-height:46px; padding:10px 12px; font-size:15px; border-radius:12px; border:1px solid #C8D3E6;
  background:#fff; color:#0B1F3A; cursor:pointer; }
.gv-btn:disabled { opacity:.4; cursor:default; }
.gv-btn.end { border-color:#F3B4AE; color:#B42318; }
.gv-fallback { font-size:15px; background:#F4F7FC; border-radius:10px; padding:10px 12px; max-width:520px; display:none; white-space:pre-wrap; }
"""

VOICE_CHAT_JS = """
const MIC_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-3.08A7 7 0 0 0 19 12h-2z"/></svg>';
const LABEL = {
  idle: '마이크를 눌러 시작하세요', starting: '마이크를 켜는 중…', listening: '듣는 중', hearing: '듣는 중',
  thinking: '생각 중', speaking: '답변 중', off: '마이크 꺼짐', error: '마이크를 켜지 못했어요',
};
const HINT = {
  idle: '편하게 말씀하시고 잠시 멈추면 자동으로 전달돼요.', starting: '브라우저가 마이크 사용을 물으면 허용해 주세요.',
  listening: '말씀하세요. 말을 마치고 잠시 멈추면 자동으로 전달돼요.', hearing: '듣고 있어요…',
  thinking: '말씀을 글로 바꾸고 답변을 만들고 있어요.', speaking: '답변을 읽는 동안에는 마이크를 잠시 꺼 둬요.',
  off: '다시 말하려면 마이크를 누르세요.', error: '',
};

function setPhase(S, phase, hint, warn) {
  S.phase = phase;
  S.el.root.dataset.phase = phase;
  S.el.status.textContent = LABEL[phase] || '';
  S.el.hint.textContent = hint !== undefined ? hint : (HINT[phase] || '');
  S.el.hint.className = 'gv-hint' + (warn ? ' warn' : '');
  const live = !!S.stream;
  S.el.micOff.disabled = !live;
  S.el.stop.disabled = phase !== 'speaking';
  S.el.mic.setAttribute('aria-label', live ? '마이크 끄기' : '마이크 켜기');
}

function micEnabled(S, on) {
  if (S.stream) S.stream.getAudioTracks().forEach(t => { t.enabled = on; });
}

function stopStream(S) {
  try { if (S.proc) { S.proc.onaudioprocess = null; S.proc.disconnect(); } } catch (_) {}
  try { if (S.src) S.src.disconnect(); } catch (_) {}
  try { if (S.stream) S.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
  try { if (S.ctx && S.ctx.state !== 'closed') S.ctx.close(); } catch (_) {}
  S.proc = S.src = S.stream = S.ctx = null;
  S.frames = []; S.pre = [];
}

function stopSpeech(S) {
  S.speakToken++;
  try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
}

function resetVad(S) {
  S.inSpeech = false; S.loud = 0; S.speechMs = 0; S.silenceMs = 0; S.warmMs = 0; S.frames = []; S.pre = [];
}

function listen(S) {
  if (!S.stream || S.ended) { setPhase(S, 'off'); return; }
  resetVad(S); S.idleMs = 0;
  micEnabled(S, true);
  setPhase(S, 'listening');
}

function micOff(S, hint) {
  clearTimeout(S.replyTimer);
  stopStream(S);
  S.awaiting = 0;
  setPhase(S, 'off', hint);
}

function pickVoice() {
  const synth = window.speechSynthesis;
  if (!synth) return null;
  const ko = synth.getVoices().filter(v => (v.lang || '').toLowerCase().replace('_', '-').startsWith('ko'));
  return ko.find(v => v.localService && v.default) || ko.find(v => v.localService) || null;
}

function chunks(text) {
  const parts = text.match(/[^.!?。\\n]+[.!?。]?/g) || [text];
  const out = []; let cur = '';
  for (const p of parts) { if ((cur + p).length > 180 && cur) { out.push(cur); cur = ''; } cur += p; }
  if (cur.trim()) out.push(cur);
  return out;
}

function afterSpeak(S, mine) {
  if (mine !== S.speakToken || S.phase !== 'speaking') return;
  setTimeout(() => { if (S.phase === 'speaking' && mine === S.speakToken) listen(S); }, 600);
}

function speak(S, text) {
  micEnabled(S, false);
  S.el.fallback.style.display = 'none';
  const synth = window.speechSynthesis;
  const voice = pickVoice();
  if (!synth || typeof SpeechSynthesisUtterance === 'undefined' || !voice) {
    S.el.fallback.textContent = text;
    S.el.fallback.style.display = 'block';
    setPhase(S, 'speaking', '이 기기에는 한국어 읽기 음성이 없어 답변을 글로 보여 드려요.');
    const mine = ++S.speakToken;
    setTimeout(() => afterSpeak(S, mine), 1500);
    return;
  }
  synth.cancel();
  const mine = ++S.speakToken;
  setPhase(S, 'speaking');
  const list = chunks(text || '');
  list.forEach((part, i) => {
    const u = new SpeechSynthesisUtterance(part);
    u.voice = voice; u.lang = voice.lang || 'ko-KR'; u.rate = 1.0;
    u.onerror = (e) => {
      if (mine !== S.speakToken || e.error === 'interrupted' || e.error === 'canceled') return;
      S.el.fallback.textContent = text; S.el.fallback.style.display = 'block';
      S.el.hint.textContent = '답변을 소리로 읽지 못해 글로 보여 드려요.';
      afterSpeak(S, mine);
    };
    if (i === list.length - 1) u.onend = () => afterSpeak(S, mine);
    synth.speak(u);
  });
}

function downsample(samples, inRate, outRate) {
  if (inRate <= outRate) return samples;
  const ratio = inRate / outRate, n = Math.floor(samples.length / ratio), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(samples.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = a; j < b; j++) sum += samples[j];
    out[i] = sum / Math.max(1, b - a);
  }
  return out;
}

function wavBase64(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data');
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  const bytes = new Uint8Array(buf); let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function finishTurn(S) {
  micEnabled(S, false);
  const total = S.frames.reduce((n, f) => n + f.length, 0), all = new Float32Array(total);
  let off = 0; for (const f of S.frames) { all.set(f, off); off += f.length; }
  const audio = wavBase64(downsample(all, S.ctx.sampleRate, 16000), 16000);
  resetVad(S);
  S.seq += 1; S.awaiting = S.seq;
  setPhase(S, 'thinking');
  clearTimeout(S.replyTimer);
  S.replyTimer = setTimeout(() => {
    if (S.awaiting === S.seq && S.phase === 'thinking') { S.awaiting = 0; listen(S); S.el.hint.textContent = '응답이 늦어요. 다시 말씀해 주세요.'; }
  }, 120000);
  S.setTrigger('utterance', {sid: S.sid, seq: S.seq, audio});
}

function onFrame(S, input) {
  if (S.phase !== 'listening' && S.phase !== 'hearing') return;
  const frameMs = input.length / S.ctx.sampleRate * 1000;
  let sum = 0; for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
  const rms = Math.sqrt(sum / input.length);
  S.warmMs += frameMs;
  // 마이크를 다시 켠 직후 0.3초는 잔향으로 보고 버린다. 그 사이 바로 말을 시작해도 기준이 높아지지 않게
  // 조용한 소리만 배경 소음 기준에 반영하고, 기준값도 상한(0.02)을 둔다.
  if (S.warmMs < 300) { if (rms < 0.02) S.floor = Math.min(0.02, S.floor * 0.7 + rms * 0.3); return; }
  const copy = new Float32Array(input);
  const startTh = Math.max(0.02, S.floor * 3), endTh = Math.max(0.012, S.floor * 2);
  if (!S.inSpeech) {
    S.pre.push(copy); if (S.pre.length > 4) S.pre.shift();
    if (rms > startTh) S.loud += 1; else { S.loud = 0; S.floor = Math.min(0.02, S.floor * 0.95 + rms * 0.05); }
    if (S.loud >= 2) {
      S.inSpeech = true; S.frames = S.pre.slice(); S.speechMs = frameMs * S.frames.length; S.silenceMs = 0;
      setPhase(S, 'hearing');
    } else {
      S.idleMs += frameMs;
      if (S.idleMs > 45000) micOff(S, '한동안 말씀이 없어 마이크를 껐어요. 다시 말하려면 마이크를 누르세요.');
    }
    return;
  }
  S.frames.push(copy); S.speechMs += frameMs;
  S.silenceMs = rms < endTh ? S.silenceMs + frameMs : 0;
  if (S.silenceMs >= 1300) {
    if (S.speechMs - S.silenceMs < 400) { resetVad(S); S.warmMs = 300; setPhase(S, 'listening'); return; }  // 짧은 잡음
    finishTurn(S);
  } else if (S.speechMs >= MAX_MS) {
    finishTurn(S);
  }
}

let MAX_MS = 40000;

async function start(S) {
  S.ended = false;
  S.el.fallback.style.display = 'none';
  // iPhone Safari: 누른 순간에 소리 재생과 오디오 처리를 깨워 둬야 나중에 답변을 읽을 수 있다.
  try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; window.speechSynthesis.speak(u); } catch (_) {}
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setPhase(S, 'error', '이 브라우저에서는 음성 대화를 쓸 수 없어요. 아래 \\'글 채팅으로\\'를 눌러 글로 대화해 주세요.', true);
    return;
  }
  let ctx;
  try { ctx = new (window.AudioContext || window.webkitAudioContext)(); ctx.resume(); } catch (_) {}
  setPhase(S, 'starting');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}});
  } catch (e) {
    try { ctx && ctx.close(); } catch (_) {}
    const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
    setPhase(S, 'error', denied
      ? '마이크 권한이 꺼져 있어요. 주소창의 자물쇠(또는 설정)에서 마이크를 허용한 뒤 다시 누르거나, 글 채팅을 이용해 주세요.'
      : '마이크를 켜지 못했어요. 다른 앱이 마이크를 쓰고 있는지 확인하거나 글 채팅을 이용해 주세요.', true);
    return;
  }
  if (S.ended || !S.mounted) { stream.getTracks().forEach(t => t.stop()); try { ctx && ctx.close(); } catch (_) {} return; }
  S.stream = stream; S.ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
  try { await S.ctx.resume(); } catch (_) {}
  S.src = S.ctx.createMediaStreamSource(stream);
  S.proc = S.ctx.createScriptProcessor(4096, 1, 1);
  S.proc.onaudioprocess = (e) => onFrame(S, e.inputBuffer.getChannelData(0));
  S.src.connect(S.proc); S.proc.connect(S.ctx.destination);   // 출력은 0으로 채워져 소리가 나지 않는다
  S.floor = 0.008;
  stream.getAudioTracks().forEach(t => { t.onended = () => { if (S.stream === stream) micOff(S, '마이크 연결이 끊겼어요. 다시 누르세요.'); }; });
  listen(S);
}

function endAll(S, notify) {
  S.ended = true;
  clearTimeout(S.replyTimer);
  stopSpeech(S);
  stopStream(S);
  S.awaiting = 0;
  if (S.el) setPhase(S, 'off', '대화를 마쳤어요.');
  if (notify) S.setTrigger('end', Date.now());
}

function build(S, root) {
  const box = document.createElement('div');
  box.className = 'gv';
  box.innerHTML = '<button class="gv-mic" type="button"></button><div class="gv-status" role="status" aria-live="polite"></div>'
    + '<div class="gv-hint"></div><div class="gv-fallback"></div>'
    + '<div class="gv-row"><button class="gv-btn micoff" type="button">마이크 끄기</button>'
    + '<button class="gv-btn stop" type="button">답변 정지</button><button class="gv-btn end" type="button">대화 종료</button></div>';
  // Streamlit이 넣어 둔 스타일(<style>)은 남기고 부품만 붙인다.
  root.querySelectorAll('.gv').forEach(n => n.remove());
  root.appendChild(box);
  S.el = {root: box, mic: box.querySelector('.gv-mic'), status: box.querySelector('.gv-status'), hint: box.querySelector('.gv-hint'),
          fallback: box.querySelector('.gv-fallback'), micOff: box.querySelector('.micoff'), stop: box.querySelector('.stop'), end: box.querySelector('.end')};
  S.el.mic.innerHTML = MIC_SVG;
  S.el.mic.onclick = () => {
    if (S.phase === 'speaking') { stopSpeech(S); listen(S); return; }
    if (S.stream && (S.phase === 'listening' || S.phase === 'hearing')) { micOff(S); return; }
    if (!S.stream && S.phase !== 'starting') start(S);
  };
  S.el.micOff.onclick = () => { stopSpeech(S); micOff(S); };
  S.el.stop.onclick = () => { stopSpeech(S); if (S.stream) listen(S); else setPhase(S, 'off'); };
  S.el.end.onclick = () => endAll(S, true);
  setPhase(S, 'idle');
}

function applyData(S, data) {
  const reply = data && data.reply, notice = data && data.notice;
  if (reply && reply.sid === S.sid && reply.id === S.awaiting) {
    S.awaiting = 0; clearTimeout(S.replyTimer);
    speak(S, String(reply.text || ''));
  } else if (notice && notice.sid === S.sid && notice.id === S.awaiting) {
    S.awaiting = 0; clearTimeout(S.replyTimer);
    if (notice.kind === 'retry' && S.stream) { listen(S); setPhase(S, 'listening', String(notice.text || ''), true); }
    else micOff(S, String(notice.text || ''));
  }
}

export default function({data, parentElement, setTriggerValue}) {
  let S = parentElement.__gv;
  if (!S) {
    S = parentElement.__gv = {sid: Math.random().toString(36).slice(2, 12), seq: 0, awaiting: 0, speakToken: 0, floor: 0.008,
                               frames: [], pre: [], mounted: true, ended: false};
    build(S, parentElement);
    S.onHide = () => endAll(S, false);
    window.addEventListener('pagehide', S.onHide);
    // 자동 시험용 읽기 전용 상태(마이크·소리 값은 담지 않는다)
    window.__gosoemiVoiceState = () => ({phase: S.phase, seq: S.seq, awaiting: S.awaiting,
      micLive: !!(S.stream && S.stream.getAudioTracks().some(t => t.readyState === 'live')),
      micOn: !!(S.stream && S.stream.getAudioTracks().some(t => t.enabled)),
      speaking: !!(window.speechSynthesis && window.speechSynthesis.speaking)});
  }
  S.setTrigger = setTriggerValue;
  if (data && data.max_seconds) MAX_MS = data.max_seconds * 1000;
  applyData(S, data);
  return () => {
    S.mounted = false;
    endAll(S, false);
    window.removeEventListener('pagehide', S.onHide);
    if (parentElement.__gv === S) delete parentElement.__gv;
  };
}
"""
