from typing import Optional, TypeVar, Union, ItemsView

JOB_NAME = TypeVar('JOB_NAME', bound=str)
JOBID = TypeVar('JOBID', bound=int)
INNER = Union[JOB_NAME, str]
OUTER = Union[JOBID, int]


class JobsMapping:
    """Manage a mapping between job's inner id (str) and its outer id (int)
    """
    def __init__(self):
        self._mapping: dict[OUTER, Optional[INNER]] = dict()

    def insert(self, inner: INNER):
        self._mapping[self._avail_outer] = inner

    def items(self) -> ItemsView[OUTER, INNER]:
        return self._mapping.items()

    def get_inner_id(self, outer: OUTER, default=None) -> Optional[INNER]:
        return self._mapping.get(outer, default)

    def get_outer_id(self, inner: INNER, default=None) -> Optional[OUTER]:
        for _outer, _inner in self._mapping.items():
            if inner == _inner:
                return _outer
        return default

    def get_any_inner_id(self, default=None) -> Optional[INNER]:
        for _outer, _inner in self._mapping.items():
            if _inner is not None:
                return _inner
        return default

    def get_any_outer_id(self, default=None) -> Optional[OUTER]:
        for _outer, _inner in self._mapping.items():
            if _inner is not None:
                return _outer
        return default

    def remove_by_inner(self, inner: INNER):
        if outer := self.get_outer_id(inner):
            self._mapping[outer] = None

    def remove_by_outer(self, outer: OUTER) -> bool:
        """make the inner None, to be replaced by new jobs"""
        if outer in self._mapping:
            self._mapping[outer] = None
            return True
        return False

    def num_entries(self) -> int:
        return len(list(filter(None, self._mapping.values())))

    def __len__(self):
        return self.num_entries()

    @property
    def _avail_outer(self) -> OUTER:
        for outer, inner in self._mapping.items():
            if inner is None:
                return outer
        return len(self._mapping) + 1

    def __repr__(self):
        return f'JobsMapping({self._mapping.__repr__()})'

