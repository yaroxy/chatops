#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkspaceConfig(BaseModel):
    path: Path

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        value = value.expanduser().resolve()

        if not value.exists():
            raise ValueError(f"workspace path does not exist: {value}")

        if not value.is_dir():
            raise ValueError(f"workspace path is not a directory: {value}")

        return value


class ChatOpsConfig(BaseModel):
    default_workspace: str
    default_agent: str = "plan"

    # TOML 里是 default_providerID / default_modelID
    # Python 里建议用 snake_case
    default_provider_id: str = Field(alias="default_providerID")
    default_model_id: str = Field(alias="default_modelID")

    workspaces: dict[str, WorkspaceConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_default_workspace(self) -> "ChatOpsConfig":
        if self.default_workspace not in self.workspaces:
            available = ", ".join(self.workspaces.keys())
            raise ValueError(
                f"default_workspace={self.default_workspace!r} not found in workspaces. "
                f"Available workspaces: {available}"
            )

        return self


class AppConfig(BaseModel):
    chatops: ChatOpsConfig


def load_config(config_path: str | Path) -> AppConfig:
    config_path = Path(config_path).expanduser().resolve()

    with config_path.open("rb") as f:
        raw_config = tomllib.load(f)

    return AppConfig.model_validate(raw_config)

"""
usage: 
    python tests/test_toml.py
"""
if __name__ == "__main__":
    config: AppConfig = load_config("tests/test.toml")
    print(config.model_dump_json(indent=2))

