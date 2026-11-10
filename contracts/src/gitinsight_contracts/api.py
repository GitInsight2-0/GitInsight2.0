"""인증·레포·분석 API 계약 (/api/me, /api/repos, /api/analyses)."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from gitinsight_contracts.base import Contract
from gitinsight_contracts.common import CommitSha


class Me(Contract):
    user_id: str
    login: str
    avatar_url: str


class Repo(Contract):
    repo_id: int
    full_name: str
    private: bool
    default_branch: str


class AnalysisCreate(Contract):
    repo_id: int


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    STAGE1 = "stage1"
    STAGE2 = "stage2"
    DONE = "done"
    FAILED = "failed"


class AnalysisCreated(Contract):
    analysis_id: str
    status: AnalysisStatus
    cached: bool


class AnalysisState(Contract):
    status: AnalysisStatus
    progress: Annotated[float, Field(ge=0, le=1)]
    commit_sha: CommitSha | None
    error: str | None = None
