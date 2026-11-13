"""Small, CPU-only operator-level Q learner for *verified* packing operators.

This policy never emits coordinates or decides geometric feasibility. JSON is
data, never executable code. A frozen policy plus a seed and iteration budget
reproduces decisions; wall-time bounded searches are inherently timing-dependent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random

import numpy as np


ACTIONS = ('compact', 'relax', 'top', 'cluster', 'random',
           'height_restart', 'random_restart', 'legacy')
FEATURES = ('bias', 'placed', 'best_placed', 'height', 'best_height',
            'material_density', 'concave_fraction', 'model_diversity',
            'quantity', 'stagnation', 'phase', 'gap_ratio', 'complete')
SCHEMA = 'powdernest.operator-q.v1'
CONTEXT_FEATURES = (5, 6, 8, 11)  # static density, non-cuboids, quantity, gap
CONTEXT_RADIUS = .2


class OperatorPolicy:
    """Linear off-policy Q learning, discounted TD(0), bounded features/weights."""

    def __init__(self, weights=None, *, updates=0, episodes=0, contexts=None):
        self.weights = np.zeros((len(ACTIONS), len(FEATURES)), dtype=float) if weights is None else np.array(weights, dtype=float, copy=True)
        if self.weights.shape != (len(ACTIONS), len(FEATURES)) or not np.isfinite(self.weights).all() or np.any(np.abs(self.weights) > 100):
            raise ValueError('학습 정책의 가중치 크기/값이 올바르지 않습니다')
        for value in (updates, episodes):
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError('학습 횟수가 올바르지 않습니다')
        self.updates, self.episodes = updates, episodes
        self.gamma, self.alpha = .8, .08
        contexts = [] if contexts is None else contexts
        if not isinstance(contexts, list) or len(contexts) > 512:
            raise ValueError('학습 범위 기록이 올바르지 않습니다')
        array = np.asarray(contexts, dtype=float)
        if len(contexts) and (array.shape != (len(contexts), len(CONTEXT_FEATURES))
                              or not np.isfinite(array).all() or np.any((array < 0) | (array > 1))):
            raise ValueError('학습 범위 기록이 올바르지 않습니다')
        self.contexts = [list(map(float, row)) for row in contexts]

    def clone(self):
        return OperatorPolicy(self.weights, updates=self.updates, episodes=self.episodes, contexts=self.contexts)

    def observe_context(self, state):
        context = self._features(state)[list(CONTEXT_FEATURES)]
        if not self.contexts or np.min(np.max(np.abs(np.asarray(self.contexts) - context), axis=1)) > 1e-4:
            self.contexts.append(context.tolist())
            self.contexts = self.contexts[-512:]

    def supports(self, state):
        # An explicitly blank policy remains available as an exploration control.
        # Learned checkpoints carry observed contexts and can abstain on OOD jobs.
        if not self.contexts:
            return True, None
        context = self._features(state)[list(CONTEXT_FEATURES)]
        distance = float(np.min(np.max(np.abs(np.asarray(self.contexts) - context), axis=1)))
        return distance <= CONTEXT_RADIUS, distance

    @staticmethod
    def _features(state):
        state = np.asarray(state, dtype=float)
        if state.shape != (len(FEATURES),) or not np.isfinite(state).all() or np.any(np.abs(state) > 1.000001):
            raise ValueError('학습 상태는 유한한 정규화 벡터여야 합니다')
        return state

    def choose(self, state, rng: random.Random, available=range(len(ACTIONS)), epsilon=.12):
        state = self._features(state)
        available = tuple(available)
        if not available or any(type(a) is not int or a not in range(len(ACTIONS)) for a in available):
            raise ValueError('학습 작업 목록이 올바르지 않습니다')
        if not 0 <= epsilon <= 1:
            raise ValueError('epsilon must be in [0,1]')
        if rng.random() < epsilon:
            return rng.choice(available)
        scores = self.weights @ state
        best = max(scores[a] for a in available)
        # Seeded ties avoid permanently starving all but the first operator.
        return rng.choice([a for a in available if scores[a] >= best - 1e-12])

    def update(self, state, action, reward, next_state, *, terminal=False,
               available=range(len(ACTIONS))):
        state, next_state = self._features(state), self._features(next_state)
        if type(action) is not int or action not in range(len(ACTIONS)) or not np.isfinite(reward):
            raise ValueError('학습 전이가 올바르지 않습니다')
        future = 0. if terminal else max((self.weights @ next_state)[list(available)], default=0.)
        target = float(np.clip(reward, -25, 25)) + self.gamma * future
        error = float(np.clip(target - self.weights[action] @ state, -25, 25))
        self.weights[action] += self.alpha * error * state / max(1., float(state @ state))
        np.clip(self.weights, -100, 100, out=self.weights)
        self.updates += 1
        return error

    def to_dict(self):
        return {'schema': SCHEMA, 'actions': list(ACTIONS), 'features': list(FEATURES),
                'weights': self.weights.tolist(), 'updates': self.updates,
                'episodes': self.episodes, 'gamma': self.gamma, 'alpha': self.alpha,
                'contexts': self.contexts}

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()

    def save(self, path):
        import os
        import tempfile
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
        if path.stat().st_size > 1_000_000:
            raise ValueError('학습 정책 파일이 너무 큽니다')
        return cls.from_dict(json.loads(path.read_text(encoding='utf-8')))

    @classmethod
    def from_dict(cls, data):
        """Validate external and bundled checkpoints with the same schema."""
        if not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('actions') != list(ACTIONS) or data.get('features') != list(FEATURES):
            raise ValueError('지원하지 않는 학습 정책 형식입니다')
        if data.get('gamma') != .8 or data.get('alpha') != .08:
            raise ValueError('지원하지 않는 학습 매개변수입니다')
        return cls(data['weights'], updates=data['updates'], episodes=data['episodes'], contexts=data.get('contexts'))


def default_policy():
    """An independent copy of the supplied checkpoint, used without training."""
    from .pretrained_policy import POLICY
    return OperatorPolicy.from_dict(POLICY)


def reward(before, after, requested, box_height):
    """Lexicographic incumbent progress; missing parts cannot earn height reward."""
    count_gain = before[0] - after[0]
    if count_gain:
        return 20. * count_gain / max(1, requested) + (5. if -after[0] == requested else 0.)
    if -after[0] == requested:
        return 4. * max(0., before[1] - after[1]) / max(1., box_height) - .001
    return -.001
