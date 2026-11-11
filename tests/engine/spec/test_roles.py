"""Roles: every role a matcher may declare has a budget rank."""

from typing import get_args

from ci_doctor.engine import RANK, Role


def test_every_role_has_a_rank():
    """`role` is a schema Literal and `RANK` a dict; a role in one only is a KeyError mid-run."""
    assert set(RANK) == set(get_args(Role))
