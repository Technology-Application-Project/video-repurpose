from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: Literal["Researcher", "General public", "Kids"] = "General public"
    aspect: Literal["16:9", "9:16", "Original", "1:1"] = "16:9"
    model: str = Field(default="qwen2.5:latest", min_length=1, max_length=100)
    whisper_model: Literal["small", "distil-large-v3", "large-v3"] = "small"
    min_seconds: float = Field(default=6, ge=3, le=20)
    max_seconds: float = Field(default=15, ge=5, le=45)
    source_url: str = Field(default="", max_length=2000)
    automatic: bool = True

    @model_validator(mode="after")
    def ordered(self):
        if self.min_seconds > self.max_seconds:
            raise ValueError("Minimum clip duration must not exceed maximum.")
        return self


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Analyze(Action):
    options: Options


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(max_length=300)
    takeaway: str = Field(default="", max_length=1000)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class Selection(Action):
    clips: list[Clip] = Field(min_length=1, max_length=5)


class Approval(Action):
    reviewer: str = Field(min_length=1, max_length=120)
    attested: bool