from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import tomlkit
from flatdict import FlatDict
from freezegun import freeze_time

from tests.const import (
    DEFAULT_BRANCH_NAME,
)
from tests.fixtures.repos.github_flow import (
    repo_w_github_flow_w_default_release_n_branch_update_merge_conventional_commits,
    repo_w_github_flow_w_default_release_n_branch_update_merge_emoji_commits,
    repo_w_github_flow_w_default_release_n_branch_update_merge_scipy_commits,
)
from tests.util import temporary_working_directory

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import MagicMock

    from requests_mock import Mocker

    from tests.e2e.cmd_version.bump_version.conftest import (
        InitMirrorRepo4RebuildFn,
        RunPSReleaseFn,
    )
    from tests.e2e.conftest import GetSanitizedChangelogContentFn
    from tests.fixtures.example_project import ExProjectDir
    from tests.fixtures.git_repo import (
        BuildRepoFromDefinitionFn,
        BuildSpecificRepoFn,
        CommitConvention,
        GetGitRepo4DirFn,
        RepoActionConfigure,
        RepoActionRelease,
        RepoActions,
        SplitRepoActionsByReleaseTagsFn,
    )


@pytest.mark.xfail(
    reason="Should pass after [#1252](https://github.com/python-semantic-release/python-semantic-release/issues/1252) is fixed",
)
@pytest.mark.parametrize(
    "repo_fixture_name",
    [
        pytest.param(repo_fixture_name, marks=pytest.mark.comprehensive)
        for repo_fixture_name in [
            repo_w_github_flow_w_default_release_n_branch_update_merge_conventional_commits.__name__,
            repo_w_github_flow_w_default_release_n_branch_update_merge_emoji_commits.__name__,
            repo_w_github_flow_w_default_release_n_branch_update_merge_scipy_commits.__name__,
        ]
    ],
)
def test_github_flow_repo_w_default_release_n_branch_update_merge(
    repo_fixture_name: str,
    run_psr_release: RunPSReleaseFn,
    build_github_flow_repo_w_default_release_n_branch_update_merge: BuildSpecificRepoFn,
    split_repo_actions_by_release_tags: SplitRepoActionsByReleaseTagsFn,
    init_mirror_repo_for_rebuild: InitMirrorRepo4RebuildFn,
    example_project_dir: ExProjectDir,
    git_repo_for_directory: GetGitRepo4DirFn,
    build_repo_from_definition: BuildRepoFromDefinitionFn,
    mocked_git_push: MagicMock,
    post_mocker: Mocker,
    default_tag_format_str: str,
    version_py_file: Path,
    get_sanitized_md_changelog_content: GetSanitizedChangelogContentFn,
    get_sanitized_rst_changelog_content: GetSanitizedChangelogContentFn,
):
    # build target repo into a temporary directory
    target_repo_dir = example_project_dir / repo_fixture_name
    commit_type: CommitConvention = (
        repo_fixture_name.split("commits", 1)[0].split("_")[-2]  # type: ignore[assignment]
    )
    target_repo_definition = (
        build_github_flow_repo_w_default_release_n_branch_update_merge(
            repo_name=repo_fixture_name,
            commit_type=commit_type,
            dest_dir=target_repo_dir,
        )
    )
    target_git_repo = git_repo_for_directory(target_repo_dir)
    target_repo_pyproject_toml = FlatDict(
        tomlkit.loads((target_repo_dir / "pyproject.toml").read_text(encoding="utf-8")),
        delimiter=".",
    )
    tag_format_str: str = target_repo_pyproject_toml.get(  # type: ignore[assignment]
        "tool.semantic_release.tag_format",
        default_tag_format_str,
    )

    # split repo actions by release actions
    releasetags_2_steps: dict[str, list[RepoActions]] = (
        split_repo_actions_by_release_tags(target_repo_definition, tag_format_str)
    )
    configuration_step: RepoActionConfigure = releasetags_2_steps.pop("")[0]  # type: ignore[assignment]

    # Create the mirror repo directory
    mirror_repo_dir = init_mirror_repo_for_rebuild(
        mirror_repo_dir=(example_project_dir / "mirror"),
        configuration_step=configuration_step,
    )
    mirror_git_repo = git_repo_for_directory(mirror_repo_dir)

    # rebuild repo from scratch stopping before each release tag
    for curr_release_tag, steps in releasetags_2_steps.items():
        # make sure mocks are clear
        mocked_git_push.reset_mock()
        post_mocker.reset_mock()

        # Extract expected result from target repo
        head_reference_name = (
            curr_release_tag
            if curr_release_tag != "Unreleased"
            else DEFAULT_BRANCH_NAME
        )
        target_git_repo.git.checkout(head_reference_name, detach=True)
        expected_md_changelog_content = get_sanitized_md_changelog_content(
            repo_dir=target_repo_dir
        )
        expected_rst_changelog_content = get_sanitized_rst_changelog_content(
            repo_dir=target_repo_dir
        )
        expected_pyproject_toml_content = (
            target_repo_dir / "pyproject.toml"
        ).read_text()
        expected_version_file_content = (target_repo_dir / version_py_file).read_text()
        expected_release_commit_text = target_git_repo.head.commit.message

        # In our repo env, start building the repo from the definition
        build_repo_from_definition(
            dest_dir=mirror_repo_dir,
            repo_construction_steps=steps[:-1],  # stop before the release step
        )
        release_action_step: RepoActionRelease = steps[-1]  # type: ignore[assignment]

        # Act: run PSR on the repo instead of the RELEASE step
        with freeze_time(
            release_action_step["details"]["datetime"]
        ), temporary_working_directory(mirror_repo_dir):
            run_psr_release(
                next_version_str=release_action_step["details"]["version"],
                git_repo=mirror_git_repo,
            )

        # take measurement after running the version command
        actual_release_commit_text = mirror_git_repo.head.commit.message
        actual_pyproject_toml_content = (mirror_repo_dir / "pyproject.toml").read_text()
        actual_version_file_content = (mirror_repo_dir / version_py_file).read_text()
        actual_md_changelog_content = get_sanitized_md_changelog_content(
            repo_dir=mirror_repo_dir
        )
        actual_rst_changelog_content = get_sanitized_rst_changelog_content(
            repo_dir=mirror_repo_dir
        )

        # Evaluate (normal release actions should have occurred as expected)
        # ------------------------------------------------------------------
        # Make sure version file is updated
        assert expected_pyproject_toml_content == actual_pyproject_toml_content
        assert expected_version_file_content == actual_version_file_content
        # Make sure changelog is updated
        assert expected_md_changelog_content == actual_md_changelog_content
        assert expected_rst_changelog_content == actual_rst_changelog_content
        # Make sure commit is created
        assert expected_release_commit_text == actual_release_commit_text
        # Make sure tag is created
        assert curr_release_tag in [tag.name for tag in mirror_git_repo.tags]
        assert mocked_git_push.call_count == 2  # 1 for commit, 1 for tag
        assert post_mocker.call_count == 1  # vcs release creation occured
