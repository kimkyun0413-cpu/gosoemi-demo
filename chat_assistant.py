"""AI 대화(글·음성) 규칙과 도우미. Streamlit에 의존하지 않는다.

- 대화 기록은 서버 메모리(이 브라우저 탭의 세션)에만 두고 DB에 저장하지 않는다.
- 사건 정보 정리는 이용자가 쓴 말만 근거로 한다. AI 답변에 나온 예시나 추측은 사실로 쓰지 않는다.
- 답변 듣기는 브라우저가 '기기 안의 음성(localService)'이라고 알려 주는 한국어 음성만 쓴다.
  원격 음성(예: 일부 데스크톱 Chrome의 'Google 한국의')은 답변 글이 외부 음성 서버로 갈 수 있어 쓰지 않는다.
"""

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
    "답변 듣기는 이 기기(브라우저·운영체제)에 들어 있는 한국어 음성으로 기기 안에서 읽어 드립니다. "
    "답변 글을 외부 음성 서비스로 보내지 않으며, 기기에 한국어 음성이 없으면 듣기를 쓸 수 없습니다."
)


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


# 답변 듣기 컴포넌트(브라우저에서 실행). 마이크는 쓰지 않는다.
SPEECH_COMPONENT_JS = """
export default function({data, parentElement}) {
  const synth = window.speechSynthesis;
  const box = document.createElement('div');
  box.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:14px;';
  const status = document.createElement('span');
  status.style.cssText = 'color:#555;';
  const play = document.createElement('button');
  const stop = document.createElement('button');
  for (const b of [play, stop]) {
    b.type = 'button';
    b.style.cssText = 'padding:6px 14px;border:1px solid #c8d3e6;border-radius:8px;background:#fff;cursor:pointer;font-size:14px;min-height:36px;';
  }
  play.textContent = '▶ 답변 듣기';
  stop.textContent = '■ 정지';
  box.append(play, stop, status);
  parentElement.replaceChildren(box);

  if (!synth || typeof SpeechSynthesisUtterance === 'undefined') {
    play.disabled = true; stop.disabled = true;
    status.textContent = '이 브라우저는 답변 듣기를 지원하지 않습니다. 글로 읽어 주세요.';
    return;
  }
  const pickVoice = () => {
    const ko = synth.getVoices().filter(v => (v.lang || '').toLowerCase().replace('_', '-').startsWith('ko'));
    // 기기 안의 음성만 쓴다. 원격 음성은 답변 글이 외부 서버로 갈 수 있다.
    return ko.find(v => v.localService && v.default) || ko.find(v => v.localService) || null;
  };
  const chunks = (text) => {
    const parts = text.match(/[^.!?。\\n]+[.!?。]?/g) || [text];
    const out = []; let cur = '';
    for (const p of parts) {
      if ((cur + p).length > 180 && cur) { out.push(cur); cur = ''; }
      cur += p;
    }
    if (cur.trim()) out.push(cur);
    return out;
  };
  let token = 0;
  const speak = () => {
    const voice = pickVoice();
    if (!voice) {
      status.textContent = '이 기기에서 쓸 수 있는 한국어 음성이 없습니다. 기기 설정에서 한국어 음성을 설치하거나 글로 읽어 주세요.';
      return;
    }
    synth.cancel();
    const mine = ++token;
    const list = chunks(data.text || '');
    status.textContent = '읽는 중…';
    list.forEach((part, i) => {
      const u = new SpeechSynthesisUtterance(part);
      u.voice = voice; u.lang = voice.lang || 'ko-KR'; u.rate = 1.0;
      u.onerror = (e) => {
        if (mine !== token) return;
        if (e.error === 'interrupted' || e.error === 'canceled') return;
        status.textContent = e.error === 'not-allowed'
          ? "자동 재생이 막혔습니다. '▶ 답변 듣기'를 눌러 주세요."
          : '읽기에 실패했습니다. 글로 읽어 주세요.';
      };
      if (i === list.length - 1) u.onend = () => { if (mine === token) status.textContent = '다 읽었습니다.'; };
      synth.speak(u);
    });
  };
  play.onclick = speak;
  stop.onclick = () => { token++; synth.cancel(); status.textContent = '멈췄습니다.'; };

  let started = false;
  const autoStart = () => {
    if (started || !data.autoplay) return;
    if (!synth.getVoices().length) return;
    started = true;
    speak();
  };
  if (data.autoplay) {
    autoStart();
    if (!started) synth.addEventListener('voiceschanged', autoStart, {once: true});
  } else {
    status.textContent = pickVoice() || !synth.getVoices().length ? '' : '이 기기에는 한국어 음성이 없어 듣기를 쓸 수 없습니다.';
  }
  // 화면에서 사라지거나(새 답변·대화 지우기·로그아웃) 탭을 떠나면 읽기를 멈춘다.
  const onHide = () => synth.cancel();
  window.addEventListener('pagehide', onHide);
  return () => { token++; synth.cancel(); window.removeEventListener('pagehide', onHide); };
}
"""

# 화면에 남은 읽기를 멈추기만 하는 컴포넌트(새 대화·지우기·로그아웃 뒤 한 번 그린다).
SPEECH_STOP_JS = """
export default function() {
  try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
}
"""
