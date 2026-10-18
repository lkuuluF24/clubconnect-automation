import pytest

from clubbot.config import ConfigError, Course, load

CONFIG = """
club_id: "67190ce4e464b78523032c8a"
common_courses:
  - { day: lundi, time: "18:45", name: "AQUA POWER" }
  - { day: mardi, time: 19:00, name: "AQUA POWER" }
accounts:
  luc:
  marie:
    extra_courses:
      - { day: mercredi, time: "19:00", name: "AQUA CYCLING" }
"""

ENV = """
# identifiants
ACCOUNT_LUC_EMAIL=luc@example.org
ACCOUNT_LUC_PASSWORD="mot de passe"
ACCOUNT_MARIE_EMAIL=marie@example.org
ACCOUNT_MARIE_PASSWORD=secret
"""


def write(tmp_path, config=CONFIG, env=ENV):
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    return tmp_path / "config.yaml", tmp_path / ".env"


def test_load(tmp_path):
    settings, accounts, webhook = load(*write(tmp_path))
    luc, marie = accounts
    assert luc.password == "mot de passe"
    # 19:00 sans guillemets est lu par YAML comme 1140 : il doit redevenir "19:00"
    assert luc.courses == [Course(0, "18:45", "AQUA POWER"), Course(1, "19:00", "AQUA POWER")]
    assert marie.courses[-1] == Course(2, "19:00", "AQUA CYCLING")
    assert settings.lock_file == tmp_path / "state" / "blocked.json"
    assert webhook is None
    assert "mot de passe" not in repr(luc)


def test_missing_credentials(tmp_path):
    with pytest.raises(ConfigError, match="ACCOUNT_MARIE"):
        load(*write(tmp_path, env="ACCOUNT_LUC_EMAIL=a\nACCOUNT_LUC_PASSWORD=b\n"))


def test_unknown_day(tmp_path):
    with pytest.raises(ConfigError, match="jour inconnu"):
        load(*write(tmp_path, config=CONFIG.replace("lundi", "monday")))


def test_interval_floor(tmp_path):
    with pytest.raises(ConfigError, match="min_interval"):
        load(*write(tmp_path, config=CONFIG + "safety:\n  min_interval_seconds: 0.2\n"))
