"""Roles: every role a matcher may declare has a budget rank."""

from typing import get_args

from ci_doctor.engine import ROLE_RANK, Role


def test_every_role_has_a_rank():
    """`role` is a schema Literal and `ROLE_RANK` a dict; a role in one only is a KeyError mid-run."""
    assert set(ROLE_RANK) == set(get_args(Role))
