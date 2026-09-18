"""Exercise default and explicit pytest selection in an independent project."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def project(pytester):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makeini("[pytest]\ntestpaths = .\n")
    pytester.makepyfile(
        test_key="""
            import pytest

            @pytest.mark.parametrize('value', [1, 2])
            def test_kept(value):
                assert value > 0

            @pytest.mark.extended
            def test_exhaustive():
                assert True
        """,
        test_specialist="def test_specialist():\n    assert True\n",
    )
    (pytester.path / "suite.toml").write_text(
        'core = ["test_key.py"]\nextended = ["test_specialist.py"]\n'
    )
    return pytester


def test_default_keeps_core_parameters_and_excludes_extended(project):
    project.runpytest_subprocess("-q").assert_outcomes(passed=2, deselected=2)


def test_full_suite_keeps_every_case(project):
    project.runpytest_subprocess("-q", "--full-suite").assert_outcomes(passed=4)


def test_explicit_path_runs_specialist_without_extra_flag(project):
    project.runpytest_subprocess("-q", "test_specialist.py").assert_outcomes(passed=1)


def test_named_core_selector_keeps_all_parameters_and_not_similar_names(project):
    (project.path / "suite.toml").write_text(
        'core = ["test_key.py::test_kept"]\nextended = ["test_specialist.py"]\n'
    )
    with (project.path / "test_key.py").open("a") as stream:
        stream.write("\ndef test_kept_but_not_selected():\n    assert True\n")
    project.runpytest_subprocess("-q").assert_outcomes(passed=2, deselected=3)


def test_new_unclassified_file_fails_instead_of_silently_losing_coverage(project):
    project.makepyfile(test_new="def test_new():\n    assert True\n")
    result = project.runpytest_subprocess("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Unclassified files:*test_new.py*"])


def test_renamed_core_test_requires_updating_the_selection(project):
    (project.path / "suite.toml").write_text(
        'core = ["test_key.py::test_missing"]\nextended = ["test_specialist.py"]\n'
    )
    result = project.runpytest_subprocess("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unmatched core selectors:*test_key.py::test_missing*"])


def test_last_failed_does_not_treat_previously_passing_files_as_missing(project):
    (project.path / "suite.toml").write_text(
        'core = ["test_key.py", "test_specialist.py"]\nextended = []\n'
    )
    path = project.path / "test_key.py"
    path.write_text(path.read_text().replace("assert value > 0", "assert value != 1"))
    project.runpytest_subprocess("-q").assert_outcomes(failed=1, passed=2, deselected=1)
    result = project.runpytest_subprocess("-q", "--lf")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert result.parseoutcomes().get("failed") == 1
    assert "unmatched core selectors" not in result.stderr.str()
