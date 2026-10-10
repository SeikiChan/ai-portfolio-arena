"""Read-only publication boundary. Never import code or state from another team's workspace."""
import json
from pathlib import Path

GPT_EXPORT = Path('C:/Users/Allen/alpaca-gpt-research/gpt_research/exports/dashboard.json')
CLAUDE_EXPORT = Path(__file__).resolve().parents[1] / 'exports' / 'dashboard.json'
# Every independent team that publishes into the shared console. A team appears here by its
# published file only; no team's code or state is ever imported.
TEAMS = {'gpt-independent': GPT_EXPORT, 'claude-independent': CLAUDE_EXPORT}
LIMIT = 32 * 1024 * 1024


def read_team_export(team_id, path=None):
    path = Path(path) if path is not None else TEAMS[team_id]
    if not path.exists():
        return {'team_id': team_id, 'status': 'unavailable', 'rounds': [], 'note': '独立团队尚未发布结果'}
    if path.stat().st_size > LIMIT:
        raise ValueError('Team export exceeds size limit')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('team_id') != team_id or not isinstance(data.get('rounds'), list):
        raise ValueError('Invalid team publication')
    return data


def read_gpt_export(path=GPT_EXPORT):
    return read_team_export('gpt-independent', path)


def read_all_teams():
    return {'teams': [read_team_export(team_id) for team_id in sorted(TEAMS)]}
