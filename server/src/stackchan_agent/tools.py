import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from .protocol import ControlMessage, control


class FaceArgs(BaseModel):
    state: Literal["idle", "listening", "thinking", "speaking", "sleepy"] = "idle"
    emotion: Literal[
        "neutral",
        "happy",
        "excited",
        "curious",
        "surprised",
        "sad",
        "crying",
        "sleepy",
        "love",
    ] = "neutral"
    intensity: float = Field(default=0.5, ge=0, le=1)


class MotionArgs(BaseModel):
    yaw_deg: float | None = Field(default=None, ge=-35, le=35)
    pitch_deg: float | None = Field(default=None, ge=5, le=85)
    duration_ms: int = Field(default=450, ge=200, le=1_500)


class LightArgs(BaseModel):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)
    brightness: float = Field(default=0.25, ge=0, le=0.35)
    animation: Literal["solid", "pulse", "rainbow", "chase", "twinkle"] = "solid"


class RoutineArgs(BaseModel):
    name: Literal[
        "greet",
        "celebrate",
        "curious",
        "comfort",
        "dance",
        "wake_up",
        "focus",
        "good_night",
    ]
    intensity: float = Field(default=0.7, ge=0.2, le=1)
    music: bool = False


class CapturePhotoArgs(BaseModel):
    quality: int = Field(default=70, ge=40, le=85)


@dataclass(frozen=True, slots=True)
class PlannedTool:
    name: str
    arguments: dict[str, Any]
    result_summary: str


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[ControlMessage]]


async def _face(args: BaseModel) -> ControlMessage:
    parsed = FaceArgs.model_validate(args)
    return control("face.set", **parsed.model_dump())


async def _motion(args: BaseModel) -> ControlMessage:
    parsed = MotionArgs.model_validate(args)
    return control("motion.set", **parsed.model_dump())


async def _lights(args: BaseModel) -> ControlMessage:
    parsed = LightArgs.model_validate(args)
    return control("lights.set", **parsed.model_dump())


async def _routine(args: BaseModel) -> ControlMessage:
    parsed = RoutineArgs.model_validate(args)
    return control("routine.play", **parsed.model_dump())


async def _capture_photo(args: BaseModel) -> ControlMessage:
    parsed = CapturePhotoArgs.model_validate(args)
    return control("camera.capture", **parsed.model_dump())


TOOLS: dict[str, Tool] = {
    "set_face": Tool("set_face", "Set semantic face state and emotion.", FaceArgs, _face),
    "move_head": Tool("move_head", "Move head within enforced safety limits.", MotionArgs, _motion),
    "set_lights": Tool("set_lights", "Set body RGB color and animation.", LightArgs, _lights),
    "play_routine": Tool(
        "play_routine",
        "Play a safe coordinated face, head, light, and optional music routine.",
        RoutineArgs,
        _routine,
    ),
    "capture_photo": Tool(
        "capture_photo",
        (
            "Capture one privacy-visible still for an explicit photo or visual-inspection "
            "request about the user. The caller must wait for the correlated capture and "
            "local-vision result before describing what is visible."
        ),
        CapturePhotoArgs,
        _capture_photo,
    ),
}


def _face_command_requested(text: str, language: str) -> bool:
    if language == "ja":
        return bool(
            re.search(r"(?:顔|表情).*(?:して|見せて|なって|にして)", text)
            or re.search(
                r"(?:笑顔|泣き顔|泣いて(?:いる|る)顔|涙の顔|悲しそう|眠そう|不思議そう)"
                r".*(?:して|見せて|なって)",
                text,
            )
        )
    return bool(
        re.search(
            r"^(?:please\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?"
            r"(?:make|show|do|put on|wear|look)\b.*\b(?:face|expression|smile|sad|"
            r"happy|excited|surprised|surprising|shocked|sleepy|tired|loving|neutral|"
            r"crying)\b",
            text,
        )
        or re.search(
            r"^(?:please\s+)?(?:make|show|do|put on|wear)\b.*\b(?:face|expression)\b",
            text,
        )
        or re.search(
            r"^(?:please\s+)?look\s+(?:sad|happy|excited|surprised|surprising|"
            r"shocked|sleepy|tired|loving|neutral|crying)\b",
            text,
        )
        or re.fullmatch(r"\s*(?:please\s+)?smile[.!?\s]*", text)
    )


def _explicit_visual_inspection_requested(text: str, language: str) -> bool:
    """Recognize consent-bearing requests for Stack-chan to look at the user.

    These phrases authorize one visible still. Generic capability or scene
    questions remain non-capturing so the camera cannot activate by implication.
    """
    if language == "ja":
        return any(
            phrase in text
            for phrase in (
                "私を見て",
                "僕を見て",
                "わたしを見て",
                "私どう見える",
                "私はどう見える",
                "今日の私どう",
                "今日の私はどう",
                "私の見た目",
                "僕の見た目",
                "私の服装",
                "僕の服装",
                "私の髪型",
                "僕の髪型",
            )
        )
    return bool(
        re.search(r"\blook\s+at\s+me\b", text)
        or re.search(r"\bhow\s+(?:do\s+i|am\s+i)\s+look(?:ing)?\b", text)
        or re.search(r"\bwhat\s+do\s+i\s+look\s+like\b", text)
        or re.search(
            r"\bhow\s+does\s+my\s+(?:outfit|hair|hairstyle|face|shirt|jacket|dress)\s+look\b",
            text,
        )
        or re.search(r"\b(?:can|could|would)\s+you\s+(?:please\s+)?(?:look\s+at|see)\s+me\b", text)
    )


def unsupported_action_feedback(transcript: str, language: str) -> list[str]:
    """Return grounding for recognized but unavailable device actions."""
    del transcript, language
    return []


async def invoke_tool(name: str, arguments: dict[str, Any]) -> ControlMessage:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    return await tool.handler(tool.schema.model_validate(arguments))


def plan_tools(transcript: str, language: str) -> list[PlannedTool]:
    """Fast deterministic intent lane for latency-critical device actions.

    An LLM tool router can be added later, but direct bilingual phrases should
    never wait on model generation or fabricate an action result.
    """
    text = transcript.casefold()
    is_japanese = language == "ja"
    plans: list[PlannedTool] = []

    explicit_photo_request = bool(
        re.search(
            r"\b(?:take|capture|snap|shoot)\b.{0,24}\b(?:a |my |our )?"
            r"(?:photo|picture|snapshot|selfie)\b",
            text,
        )
        or re.fullmatch(r"\s*(?:photo|picture|selfie)[.!?\s]*", text)
        or any(
            phrase in text
            for phrase in (
                "写真を撮って",
                "写真撮って",
                "写真を撮影して",
                "撮影して",
                "自撮りして",
            )
        )
        or _explicit_visual_inspection_requested(text, language)
    )
    if explicit_photo_request:
        plans.append(
            PlannedTool(
                "move_head",
                {"yaw_deg": 0.0, "pitch_deg": 45.0, "duration_ms": 550},
                (
                    "撮影用に頭を正面へ向けました"
                    if is_japanese
                    else "the head physically moved into the photo pose"
                ),
            )
        )
        plans.append(
            PlannedTool(
                "capture_photo",
                {"quality": 70},
                (
                    "本体カメラで写真を撮影しました"
                    if is_japanese
                    else "the onboard camera physically captured a photo"
                ),
            )
        )

    face_emotions = (
        (
            "crying",
            (
                r"\b(?:crying|tearful)\b",
                "泣き顔",
                "泣いている顔",
                "泣いてる顔",
                "涙の顔",
            ),
            0.95,
        ),
        ("sad", (r"\b(?:sad|unhappy|worried)\b", "悲しい", "悲しそう", "心配そう"), 0.85),
        ("happy", (r"\b(?:happy|smiling|cheerful)\b", r"\bsmile\b", "嬉しい", "笑顔"), 0.9),
        ("excited", (r"\bexcited\b", "わくわく", "興奮"), 0.9),
        (
            "surprised",
            (r"\b(?:surprised|surprising|shocked)\b", "驚いた", "びっくり"),
            0.9,
        ),
        ("sleepy", (r"\b(?:sleepy|tired)\b", "眠そう", "眠い"), 0.75),
        ("love", (r"\b(?:loving|love|adoring)\b", "大好き", "愛情"), 1.0),
        ("neutral", (r"\bneutral\b", "普通の顔", "真顔"), 0.5),
    )
    if _face_command_requested(text, language):
        for emotion, patterns, intensity in face_emotions:
            if any(re.search(pattern, text) for pattern in patterns):
                plans.append(
                    PlannedTool(
                        "set_face",
                        {"state": "idle", "emotion": emotion, "intensity": intensity},
                        (
                            f"{emotion}の表情が本体で完了しました"
                            if is_japanese
                            else f"the {emotion} face physically completed"
                        ),
                    )
                )
                break

    english_music_command = bool(
        re.search(r"\b(?:play|start|put on|make)\b.{0,24}\bmusic\b", text)
        or re.search(
            r"\b(?:play|start|sing)\b.{0,24}\b(?:a )?"
            r"(?:song|tune|melody|beat|fanfare|chiptune|lo-?fi)\b",
            text,
        )
        or re.search(r"\b(?:play|make)\b.{0,24}\b(?:longer|long)\b.{0,12}\bmusic\b", text)
        or re.search(
            r"(?:^|\bplease\b|\bstack(?:-| )?chan\b[,. ]*)\s*(?:dance|do a dance)\b",
            text,
        )
        or re.fullmatch(r"\s*(?:dance|play music)[.!?\s]*", text)
    )
    japanese_music_command = any(
        phrase in text
        for phrase in (
            "ダンスして",
            "踊って",
            "音楽をかけ",
            "音楽かけ",
            "音楽を流",
            "音楽を再生",
            "曲をかけ",
            "曲を流",
            "歌って",
            "長い音楽",
            "ファンファーレ",
            "チップチューン",
            "ローファイ",
            "落ち着く曲",
            "癒やしの曲",
            "集中用の曲",
            "目覚ましの曲",
        )
    )
    lullaby_command = bool(
        re.search(r"\b(?:play|sing)\b.{0,16}\b(?:a )?lullaby\b", text)
        or "子守唄" in text
    )
    bedtime_music_command = bool(
        lullaby_command
        or (
            (re.search(r"\b(?:bedtime|good ?night)\b", text) or "おやすみ" in text)
            and (english_music_command or japanese_music_command)
        )
    )
    music_requested = english_music_command or japanese_music_command
    music_style_routine: str | None = None
    if bedtime_music_command:
        music_style_routine = "good_night"
    elif music_requested:
        style_patterns = (
            ("celebrate", (r"\b(?:fanfare|victory|celebration)\b", "ファンファーレ", "お祝いの曲")),
            (
                "comfort",
                (
                    r"\b(?:calm|relaxing|gentle|soft)\b",
                    "落ち着く",
                    "癒やし",
                    "穏やか",
                    "リラックス",
                ),
            ),
            ("focus", (r"\b(?:focus|lo-?fi|concentration)\b", "集中", "ローファイ")),
            ("wake_up", (r"\b(?:morning|wake[ -]?up|sunrise)\b", "朝の曲", "目覚まし")),
            (
                "dance",
                (
                    r"\b(?:chiptune|upbeat|dance|energetic)\b",
                    "チップチューン",
                    "アップテンポ",
                    "ダンス",
                ),
            ),
        )
        music_style_routine = next(
            (
                routine
                for routine, patterns in style_patterns
                if any(re.search(pattern, text) for pattern in patterns)
            ),
            "dance",
        )
    # Physical routines need an imperative or a first-person emotional request.
    # Topic mentions such as "I wonder..." or "she is sad" stay conversational.
    routine_patterns = (
        ("dance", (), True),
        (
            "celebrate",
            (
                r"\b(?:please\s+)?celebrate\b",
                r"\blet(?:'s| us) celebrate\b",
                r"\bcongratulate me\b",
                "お祝いして",
                "祝って",
            ),
            True,
        ),
        (
            "comfort",
            (
                r"\bcomfort me\b",
                r"\bcheer me up\b",
                r"\b(?:i am|i'm|i feel|i'm feeling)\s+(?:sad|upset|lonely)\b",
                "慰めて",
                "元気づけて",
                "私は悲しい",
                "寂しいです",
            ),
            False,
        ),
        (
            "curious",
            (
                r"\bshow me (?:a )?curious (?:face|expression)\b",
                r"\blook curious\b",
                r"\bmake (?:a |your )?curious (?:face|expression)\b",
                r"\bdo (?:a |your )?curious (?:face|routine)\b",
                "不思議そうな顔して",
                "興味津々な顔して",
            ),
            False,
        ),
        (
            "greet",
            (r"\bgreet (?:me|us|them)\b", r"\bsay hello\b", r"\bwave hello\b", "挨拶して"),
            False,
        ),
        (
            "wake_up",
            (
                r"(?:^|[.!?]\s*)good morning[.!?\s]*$",
                r"\b(?:do (?:a |your )?)?wake[ -]?up routine\b",
                r"\bwake up,? stack(?:-| )?chan\b",
                "おはよう",
                "起きて",
                "目を覚まして",
            ),
            False,
        ),
        (
            "focus",
            (
                r"\b(?:start|enter|do) focus mode\b",
                r"\blet(?:'s| us) (?:focus|concentrate)\b",
                r"\bhelp me focus\b",
                "集中モード",
                "集中しよう",
                "集中させて",
            ),
            False,
        ),
        (
            "good_night",
            (
                r"(?:^|[.!?]\s*)good ?night[.!?\s]*$",
                r"\b(?:start|do) (?:a |your )?(?:bedtime|good ?night) routine\b",
                r"\btime for bed\b",
                "おやすみ",
                "寝る時間",
            ),
            False,
        ),
    )
    for routine, patterns, music in routine_patterns:
        style_matched = music_style_routine == routine
        matched = style_matched or any(re.search(pattern, text) for pattern in patterns)
        if matched:
            routine_music = music or style_matched
            plans.append(
                PlannedTool(
                    "play_routine",
                    {"name": routine, "intensity": 0.75, "music": routine_music},
                    (
                        f"{routine}ルーティンを開始するよう依頼しました"
                        + ("（音楽付き）" if routine_music else "")
                        if is_japanese
                        else f"accepted {routine} routine"
                        + (" with music" if routine_music else "")
                    ),
                )
            )
            break

    directions = (
        ((r"\bleft\b", "左"), -24.0, 45.0, "turn left", "左を向く"),
        ((r"\bright\b", "右"), 24.0, 45.0, "turn right", "右を向く"),
        ((r"\blook up\b", "上を向"), 0.0, 25.0, "look up", "上を向く"),
        ((r"\blook down\b", "下を向"), 0.0, 65.0, "look down", "下を向く"),
        (
            (r"\bcenter\b", r"\bstraight\b", "正面"),
            0.0,
            45.0,
            "face center",
            "正面を向く",
        ),
    )
    direct_head_pose = bool(
        re.search(
            r"(?:^|\b(?:please|stack(?:-| )?chan)[, ]+)"
            r"(?:put |make |move |turn )?(?:your )?head\s+"
            r"(?:toward(?:s)?|to|at)?\s*(?:left|right|up|down|center|straight)\b",
            text,
        )
    )
    motion_command = bool(
        re.search(r"\b(?:look|turn|move|face|point|tilt)\b", text)
        or direct_head_pose
        or any(token in text for token in ("向いて", "向く", "動かして", "頭を", "顔を"))
    )
    numeric_yaw: float | None = None
    numeric_pitch: float | None = None
    yaw_match = re.search(r"\byaw(?:\s+to)?\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?|°)?", text)
    pitch_match = re.search(
        r"\bpitch(?:\s+to)?\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?|°)?", text
    )
    if yaw_match:
        numeric_yaw = max(-35.0, min(35.0, float(yaw_match.group(1))))
    if pitch_match:
        numeric_pitch = max(5.0, min(85.0, float(pitch_match.group(1))))
    horizontal_angles = (
        (r"\bleft\s+(\d+(?:\.\d+)?)\s*(?:degrees?|°)", -1),
        (r"\bright\s+(\d+(?:\.\d+)?)\s*(?:degrees?|°)", 1),
    )
    for pattern, sign in horizontal_angles:
        match = re.search(pattern, text)
        if match:
            numeric_yaw = max(-35.0, min(35.0, sign * float(match.group(1))))
            break
    for token, sign in (("左", -1), ("右", 1)):
        match = re.search(rf"{token}(?:に|へ)?\s*(\d+(?:\.\d+)?)\s*(?:度|°)", text)
        if match:
            numeric_yaw = max(-35.0, min(35.0, sign * float(match.group(1))))
            break
    motion_planned = motion_command and (numeric_yaw is not None or numeric_pitch is not None)
    if motion_planned:
        summary = (
            "指定角度へ頭を動かすよう依頼しましたが、実行は未確認です。完了したとは言わないでください。"
            if is_japanese
            else "requested the bounded head angle, but execution is unconfirmed; "
            "never claim that the motion completed"
        )
        plans.append(
            PlannedTool(
                "move_head",
                {
                    **({"yaw_deg": numeric_yaw} if numeric_yaw is not None else {}),
                    **({"pitch_deg": numeric_pitch} if numeric_pitch is not None else {}),
                    "duration_ms": 550,
                },
                summary,
            )
        )
    for patterns, yaw, pitch, english_action, japanese_action in directions:
        if motion_planned:
            break
        if motion_command and any(re.search(pattern, text) for pattern in patterns):
            summary = (
                f"{japanese_action}動作を依頼しましたが、実行は未確認です。"
                f"完了したとは言わず、『{japanese_action}ね』のように意図だけを伝えてください。"
                if is_japanese
                else f"requested {english_action}, but execution is unconfirmed; "
                f"say only that you will try, never that the motion completed"
            )
            plans.append(
                PlannedTool(
                    "move_head",
                    {
                        **({"yaw_deg": yaw} if yaw is not None else {}),
                        **({"pitch_deg": pitch} if pitch is not None else {}),
                        "duration_ms": 550,
                    },
                    summary,
                )
            )
            break

    colors = {
        "red": (255, 30, 20),
        "green": (20, 255, 60),
        "blue": (30, 90, 255),
        "pink": (255, 50, 150),
        "purple": (160, 50, 255),
        "赤": (255, 30, 20),
        "緑": (20, 255, 60),
        "青": (30, 90, 255),
        "ピンク": (255, 50, 150),
        "紫": (160, 50, 255),
    }
    mentions_lights = any(word in text for word in ("light", "lights", "led", "ライト", "光"))
    light_planned = False
    if mentions_lights:
        for color, (red, green, blue) in colors.items():
            if color in text:
                plans.append(
                    PlannedTool(
                        "set_lights",
                        {
                            "red": red,
                            "green": green,
                            "blue": blue,
                            "brightness": 0.25,
                            "animation": "pulse",
                        },
                        (
                            f"{color}のパルスライトを開始するよう依頼しました"
                            if is_japanese
                            else f"accepted {color} pulsing lights"
                        ),
                    )
                )
                light_planned = True
                break
    if mentions_lights and not light_planned:
        blink_command = bool(
            re.search(r"\b(?:blink|blinks|flash|pulse|twinkle)\b", text)
            or any(cue in text for cue in ("点滅", "ピカピカ", "光らせて"))
        )
        default_light_command = bool(
            re.search(
                r"\b(?:turn on|switch on|make|set|start)\b.{0,24}\b(?:the |your )?lights?\b",
                text,
            )
            or any(cue in text for cue in ("ライトをつけ", "ライトつけ", "光らせて"))
        )
        if blink_command or default_light_command:
            plans.append(
                PlannedTool(
                    "set_lights",
                    {
                        "red": 30,
                        "green": 90,
                        "blue": 255,
                        "brightness": 0.25,
                        "animation": "twinkle" if blink_command else "pulse",
                    },
                    (
                        "青いライト演出を開始するよう依頼しました"
                        if is_japanese
                        else "accepted a blue light animation"
                    ),
                )
            )
    return plans
