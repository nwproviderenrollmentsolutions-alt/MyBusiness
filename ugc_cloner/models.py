"""Data model for the UGC Viral Cloner pipeline.

Every field that could be a fabricated fact instead of an observation carries (or is
grouped under) an ``Evidence`` tag. Nothing downstream may print a number whose evidence
is not OBSERVED without also printing that tag — see ugc_cloner/qc.py.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------


class Evidence(StrEnum):
    """How a claim was arrived at. Never omitted on anything measurable."""

    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class Rating(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


HookType = Literal[
    "curiosity",
    "shock",
    "contrarian",
    "problem",
    "transformation",
    "demonstration",
    "confession",
    "story",
    "question",
    "unexpected_result",
    "before_after",
    "secret",
    "list",
    "challenge",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Avatar (see ugc_cloner/avatar.py for loading/locking)
# ---------------------------------------------------------------------------


class AvatarAppearance(_Model):
    face: str = ""
    hair: str = ""
    skin: str = ""
    eyes: str = ""
    facial_features: str = ""
    body_type: str = ""
    age_appearance: str = ""


class AvatarWardrobe(_Model):
    default: str = ""
    approved_variations: list[str] = Field(default_factory=list)


class AvatarVoice(_Model):
    provider: str = ""
    voice_id: str = ""
    characteristics: str = ""


class AvatarPersonality(_Model):
    energy: str = ""
    speaking_style: str = ""
    facial_expression_style: str = ""
    gesture_style: str = ""


class AvatarCamera(_Model):
    preferred_framing: str = ""
    preferred_angles: str = ""
    preferred_lens_style: str = ""


class AvatarIdentityLock(_Model):
    enabled: bool = True
    replacement_allowed: bool = False


DEFAULT_NEGATIVE_CONSTRAINTS: list[str] = [
    "different person",
    "different face",
    "altered identity",
    "random influencer",
    "stock actor",
    "celebrity likeness",
]


class AvatarProfile(_Model):
    """The source of truth for the locked identity. Loaded from
    ``avatar/avatar-profile.md`` — see ugc_cloner/avatar.py."""

    name: str = ""
    identity_lock: AvatarIdentityLock = Field(default_factory=AvatarIdentityLock)
    appearance: AvatarAppearance = Field(default_factory=AvatarAppearance)
    wardrobe: AvatarWardrobe = Field(default_factory=AvatarWardrobe)
    voice: AvatarVoice = Field(default_factory=AvatarVoice)
    personality: AvatarPersonality = Field(default_factory=AvatarPersonality)
    camera: AvatarCamera = Field(default_factory=AvatarCamera)
    negative_constraints: list[str] = Field(
        default_factory=lambda: list(DEFAULT_NEGATIVE_CONSTRAINTS)
    )
    #: Populated by the loader from what's actually on disk under avatar/reference-images
    #: and avatar/reference-video — never trust the profile text alone for this.
    reference_image_paths: list[str] = Field(default_factory=list)
    reference_video_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 1 — video analysis
# ---------------------------------------------------------------------------


class HookAnalysis(_Model):
    first_sentence: str | None = None
    first_visual: str | None = None
    curiosity_mechanism: str | None = None
    emotional_trigger: str | None = None
    hook_type: HookType | None = None
    evidence: Evidence = Evidence.UNKNOWN


class TimelineBeat(_Model):
    start_seconds: float
    end_seconds: float
    label: str
    description: str = ""


class Shot(_Model):
    start_seconds: float
    end_seconds: float
    camera_angle: str = ""
    framing: str = ""
    camera_movement: str = ""
    background: str = ""
    subject_position: str = ""
    facial_expression: str = ""
    gesture: str = ""
    on_screen_text: str = ""
    b_roll: str = ""
    transition: str = ""
    audio: str = ""


class EditingMetrics(_Model):
    total_duration_seconds: float | None = None
    cuts_per_minute: float | None = None
    average_shot_length_seconds: float | None = None
    hook_length_seconds: float | None = None
    cta_length_seconds: float | None = None
    broll_percentage: float | None = None
    talking_head_percentage: float | None = None
    evidence: Evidence = Evidence.UNKNOWN


class PerformanceProfile(_Model):
    """Presentation *style*, deliberately identity-free — never used to reproduce the
    original creator, only to inform how the locked avatar performs."""

    energy_level: str = ""
    speaking_speed: str = ""
    sentence_length: str = ""
    gesture_frequency: str = ""
    facial_expression_frequency: str = ""
    eye_contact: str = ""
    confidence_level: str = ""
    conversational_style: str = ""


class VideoAnalysis(_Model):
    source_description: str
    hook: HookAnalysis
    timeline: list[TimelineBeat] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    editing: EditingMetrics = Field(default_factory=EditingMetrics)
    performance: PerformanceProfile = Field(default_factory=PerformanceProfile)


# ---------------------------------------------------------------------------
# Phase 2/3 — viral research and framework extraction
# ---------------------------------------------------------------------------


class ResearchFinding(_Model):
    title: str
    url: str | None = None
    platform: str | None = None
    hook_summary: str | None = None
    #: Views/likes/etc. Only ever populated when evidence is OBSERVED — see
    #: ugc_cloner/research.py.
    engagement_signals: dict[str, str] = Field(default_factory=dict)
    evidence: Evidence = Evidence.UNKNOWN


class ViralFramework(_Model):
    name: str
    pattern: list[str]
    example_hook: str | None = None
    based_on: list[ResearchFinding] = Field(default_factory=list)
    evidence: Evidence = Evidence.UNKNOWN


# ---------------------------------------------------------------------------
# Phase 4/5 — creative scoring and blueprint
# ---------------------------------------------------------------------------


class ScoredAttribute(_Model):
    rating: Rating
    evidence_note: str


class CreativeScorecard(_Model):
    hook_strength: ScoredAttribute
    clarity: ScoredAttribute
    curiosity: ScoredAttribute
    demonstration_strength: ScoredAttribute
    visual_change_frequency: ScoredAttribute
    pacing: ScoredAttribute
    product_visibility: ScoredAttribute
    cta_clarity: ScoredAttribute
    format_reusability: ScoredAttribute


class CreativeBlueprint(_Model):
    concept: str
    viral_framework: ViralFramework
    scorecard: CreativeScorecard
    product: str
    campaign_objective: str
    target_duration_seconds: float = 25.0


# ---------------------------------------------------------------------------
# Phase 6/7/8 — script, shots, generation prompts (avatar-locked)
# ---------------------------------------------------------------------------


class ScriptLine(_Model):
    beat: str
    seconds: str
    on_screen_text: str | None
    voiceover: str


class AdaptedScript(_Model):
    lines: list[ScriptLine]
    cta: str


class ShotPlan(_Model):
    shot_number: int
    beat: str
    start_seconds: float
    end_seconds: float
    camera_angle: str
    framing: str
    action: str
    on_screen_text: str | None = None
    voiceover_line: str | None = None


class GenerationPrompt(_Model):
    """One vendor-agnostic prompt, ready to hand to any VideoGenProvider. The identity
    lock is embedded in the prompt text itself, not left to a caller to remember."""

    shot_number: int
    prompt_text: str
    negative_constraints: list[str]
    avatar_reference_note: str
    duration_seconds: float


class VoicePrompt(_Model):
    shot_number: int
    text: str
    voice_id: str | None


# ---------------------------------------------------------------------------
# Phase 9/10 — QC and final package
# ---------------------------------------------------------------------------


class QCFinding(_Model):
    severity: Literal["blocker", "warning"]
    message: str


class QCResult(_Model):
    passed: bool
    findings: list[QCFinding] = Field(default_factory=list)


class ProductionPackage(_Model):
    blueprint: CreativeBlueprint
    script: AdaptedScript
    shots: list[ShotPlan]
    generation_prompts: list[GenerationPrompt]
    voice_prompts: list[VoicePrompt]
    qc: QCResult
    avatar_name: str
