"""Portable build-box profiles, independent of model files and Q checkpoints."""
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import tempfile

from .types import Settings


@dataclass(frozen=True)
class BuildProfile:
    profile_id: str
    name: str
    size: tuple[float, float, float]
    gap: float = 2.
    wall_gap: float = 1.

    def __post_init__(self):
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError('프로필 ID가 필요합니다')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('프로필 이름이 필요합니다')
        checked = Settings(size=self.size, gap=self.gap, wall_gap=self.wall_gap)
        object.__setattr__(self, 'size', checked.size)
        object.__setattr__(self, 'gap', checked.gap)
        object.__setattr__(self, 'wall_gap', checked.wall_gap)

    def settings(self, base=None):
        return replace(base or Settings(), size=self.size, gap=self.gap, wall_gap=self.wall_gap)


@dataclass
class ProfileLibrary:
    profiles: list[BuildProfile]
    active_id: str
    training_ids: list[str]

    def __post_init__(self):
        if (not isinstance(self.profiles, list) or not all(isinstance(p, BuildProfile) for p in self.profiles)
                or not isinstance(self.active_id, str) or not isinstance(self.training_ids, list)
                or not all(isinstance(i, str) for i in self.training_ids)):
            raise ValueError('프로필 목록/선택 형식이 올바르지 않습니다')
        ids = [p.profile_id for p in self.profiles]
        if not ids or len(ids) != len(set(ids)) or self.active_id not in ids:
            raise ValueError('프로필 목록/선택이 올바르지 않습니다')
        if len(self.training_ids) != len(set(self.training_ids)) or any(i not in ids for i in self.training_ids):
            raise ValueError('학습 프로필 선택이 올바르지 않습니다')

    @classmethod
    def defaults(cls):
        return cls([BuildProfile('large', '대형 450 × 500 × 350', (450, 500, 350)),
                    BuildProfile('small', '소형 300 × 300 × 300', (300, 300, 300))],
                   'large', ['large', 'small'])

    def to_dict(self):
        return {'schema': 'powdernest.build-profiles.v1', 'profiles': [asdict(p) for p in self.profiles],
                'active_id': self.active_id, 'training_ids': list(self.training_ids)}

    def save(self, path):
        self.__post_init__()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError('프로필 파일이 너무 큽니다')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema') != 'powdernest.build-profiles.v1':
            raise ValueError('지원하지 않는 빌드박스 프로필 형식입니다')
        try:
            return cls([BuildProfile(**p) for p in data['profiles']], data['active_id'], data['training_ids'])
        except (KeyError, TypeError) as exc:
            raise ValueError('프로필 파일의 항목이 올바르지 않습니다') from exc
