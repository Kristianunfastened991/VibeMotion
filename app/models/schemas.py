from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WordToken(BaseModel):
    start: float
    end: float
    text: str
    probability: float | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[WordToken] = Field(default_factory=list)


class TranscriptData(BaseModel):
    language: str | None = None
    duration: float | None = None
    text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)
    words: list[WordToken] = Field(default_factory=list)


class CutRange(BaseModel):
    start: float
    end: float
    reason: str
    source: str = "source"
    handle_start: float | None = None
    handle_end: float | None = None


class CutSuggestion(BaseModel):
    start: float
    end: float
    category: Literal["silence", "filler", "retake", "trim"]
    detail: str


DesignPreset = Literal["soft-neumorphism", "frosted-glass", "warm-teal-ui", "creator-vibe", "glass", "liquid-glass", "data-panel", "bold-caption"]
MotionAnimation = Literal["none", "slide", "fade", "pop", "rise", "drop"]
MotionDirection = Literal["left", "right", "top", "bottom", "center"]
MotionEasing = Literal["expo", "power", "sine", "linear"]


class MotionSpec(BaseModel):
    id: str
    kind: Literal["glass-card", "lower-third", "caption-box"] = "glass-card"
    design_preset: DesignPreset = "soft-neumorphism"
    text: str
    start: float
    duration: float
    x: int = 80
    y: int = 120
    width: int = 760
    height: int = 240
    text_scale: float = Field(default=1.0, ge=0.35, le=3.0)
    accent: str = "#7dd3fc"
    background: str = "rgba(15, 23, 42, 0.55)"
    animation: Literal["slide-up", "slide-left", "fade", "slide-right"] = "slide-left"
    enter_animation: MotionAnimation = "slide"
    exit_animation: MotionAnimation = "slide"
    enter_from: MotionDirection = "right"
    exit_to: MotionDirection = "left"
    enter_duration: float = Field(default=0.45, ge=0.05, le=20.0)
    exit_duration: float = Field(default=0.35, ge=0.05, le=20.0)
    easing: MotionEasing = "expo"
    prompt: str | None = None
    source_type: Literal["generated", "figma"] = "generated"
    asset_path: str | None = None
    video_asset_path: str | None = None
    asset_version: str | None = None
    asset_signature: str | None = None
    figma_file_key: str | None = None
    figma_node_id: str | None = None
    figma_node_name: str | None = None
    figma_layers: list[dict[str, Any]] = Field(default_factory=list)
    motion_plan: dict[str, Any] | None = None
    motion_units: list[dict[str, Any]] = Field(default_factory=list)


class EditPlan(BaseModel):
    summary: str
    strategy: str
    estimated_duration: float
    keep_ranges: list[CutRange]
    suggestions: list[CutSuggestion] = Field(default_factory=list)
    subtitle_style: str = "bold-overlay"
    motion_notes: list[str] = Field(default_factory=list)


class ProjectState(BaseModel):
    project_id: str
    title: str
    status: str
    mode: Literal["full", "cleanup"] = "full"
    style_preset_id: str | None = None
    subtitles_enabled: bool = False
    source_video: str | None = None
    transcript: TranscriptData | None = None
    edit_plan: EditPlan | None = None
    motions: list[MotionSpec] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    undo_stack: list[dict[str, Any]] = Field(default_factory=list)
    redo_stack: list[dict[str, Any]] = Field(default_factory=list)
    current_job_id: str | None = None
    last_error: str | None = None


class MotionPromptRequest(BaseModel):
    prompt: str
    preset: DesignPreset = "soft-neumorphism"
    start: float | None = None
    duration: float | None = None
    enhance: bool = True
    motion_type: str = "auto"
    target_motion_id: str | None = None
    apply_to_all: bool = False
    variant: bool = False
    whole_video: bool = True
    auto_director: bool = False


class MotionAnimationPromptRequest(BaseModel):
    prompt: str
    mode: str = "replace"
    enhance: bool = True
    apply_to_all: bool = False
    variant: bool = False


class FigmaLayerMotionPromptRequest(BaseModel):
    layer_id: str
    prompt: str
    mode: str = "replace"


class LtxLayerVideoRequest(BaseModel):
    layer_id: str
    prompt: str
    duration: float = Field(default=4.0, ge=4.0, le=20.0)
    fps: int = Field(default=8, ge=8, le=50)
    max_side: int = Field(default=480, ge=320, le=1080)
    seed: int | None = None


class LtxLayerVideoApplyRequest(BaseModel):
    layer_id: str


class NativeMotionCuePreviewRequest(BaseModel):
    prompt: str
    start: float = Field(default=0.0, ge=0.0)
    duration: float = Field(default=4.0, ge=0.5, le=20.0)
    motion_type: str = "auto"
    variant_index: int = Field(default=0, ge=0, le=1000)
    variant_seed: str | None = None


class NativeMotionCueApplyRequest(BaseModel):
    preview_id: str


class FigmaImportRequest(BaseModel):
    figma_url: str
    access_token: str | None = None
    node_id: str | None = None
    start: float | None = None
    duration: float | None = None


class FigmaAssetsRequest(BaseModel):
    figma_url: str
    access_token: str | None = None
    node_id: str | None = None


class FigmaPluginAssetImportRequest(BaseModel):
    asset_id: str
    start: float | None = None
    duration: float | None = None


class FigmaPluginAssetsRequest(BaseModel):
    assets: list[dict[str, Any]]
    scope: str | None = None
    page: str | None = None
    session_id: str | None = None
    total: int | None = None
    complete: bool = False


class FigmaLayerUpdateRequest(BaseModel):
    layer_id: str
    patch: dict[str, Any]


class ProjectNoteRequest(BaseModel):
    note: str


class MotionUpdateRequest(BaseModel):
    text: str
    start: float
    duration: float
    render_asset: bool = True
    sync_text: bool = False
    preset: DesignPreset
    x: int
    y: int
    width: int
    height: int
    text_scale: float = Field(default=1.0, ge=0.35, le=3.0)
    accent: str
    background: str = "rgba(255, 255, 255, 0.24)"
    animation: Literal["slide-up", "slide-left", "fade", "slide-right"]
    enter_animation: MotionAnimation = "slide"
    exit_animation: MotionAnimation = "slide"
    enter_from: MotionDirection = "right"
    exit_to: MotionDirection = "left"
    enter_duration: float = Field(default=0.45, ge=0.05, le=20.0)
    exit_duration: float = Field(default=0.35, ge=0.05, le=20.0)
    easing: MotionEasing = "expo"


class ClipUpdateRequest(BaseModel):
    source_start: float
    source_end: float
    start: float | None = None


class TimelineDeleteRequest(BaseModel):
    items: list[dict[str, str]]


class TimelineSplitRequest(BaseModel):
    time: float


class ProjectActionRequest(BaseModel):
    render_preview: bool = True
    cleanup_only: bool = False
    run_analysis: bool = False


class AgentEditRequest(BaseModel):
    prompt: str = ""
    variant_seed: str | None = None
    render_preview: bool = True
    create_motion_blocks: bool = True
    style_preset_id: str | None = None
    plan_only: bool = False
    subtitles_enabled: bool = False
    cleanup_only: bool = False


class StylePresetSelectRequest(BaseModel):
    style_preset_id: str | None = None


class JobStatus(BaseModel):
    job_id: str
    project_id: str
    kind: Literal["analyze", "render", "ltx", "agent-edit"]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int = 0
    message: str = ""
    error: str | None = None
