"""Read-only input identity checks for public export entry points."""
from __future__ import annotations

import os
from pathlib import Path


def capture_input_identities(paths):
    identities=[];seen=set()
    for value in paths:
        if not value:continue
        path=Path(value).expanduser().resolve()
        key=os.path.normcase(str(path))
        if key in seen:continue
        seen.add(key)
        try:stat=path.stat();identity=(stat.st_dev,stat.st_ino)
        except OSError:identity=None
        identities.append((str(path),identity))
    return tuple(identities)


def output_targets(destination,kind='standard'):
    destination=Path(destination)
    if kind=='chitubox':return destination,Path(str(destination)+'.json')
    if kind!='standard':raise ValueError('지원하지 않는 내보내기 형식입니다.')
    return tuple(destination/name for name in ('layout.stl','placements.json','report.json','report.txt'))


def protect_destinations(inputs,targets):
    """Reject before writing, including symlinks, hardlinks and old inodes."""
    for target in targets:
        resolved=Path(target).expanduser().resolve()
        try:stat=resolved.stat();target_identity=(stat.st_dev,stat.st_ino)
        except OSError:target_identity=None
        for source,identity in inputs:
            same_path=os.path.normcase(str(resolved))==os.path.normcase(str(Path(source).resolve()))
            same_identity=identity is not None and target_identity==identity
            try:current_alias=Path(target).samefile(source)
            except OSError:current_alias=False
            if same_path or same_identity or current_alias:
                raise ValueError('입력 원본을 덮어쓸 수 없습니다. 다른 저장 위치를 선택하세요: '+str(target))


def protect_model_outputs(models,destination,kind='standard'):
    protect_destinations(capture_input_identities(model.source_path for model in models.values()),
                         output_targets(destination,kind))
