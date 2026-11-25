# Agent Context Doctor — правила для агента

CLI-линтер файлов контекста ИИ-агентов. Python 3.10+, только стандартная библиотека.

## Структура

- `agctx/parse.py` — разбор markdown: frontmatter, инлайн-код, команды, версии.
- `agctx/discover.py` — обход дерева и поиск `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `SKILL.md` и `rules/*.md`.
- `agctx/checks.py` — семь проверок (`dead-path`, `dead-command`, `version-drift`, `skill-frontmatter`, `duplicate-rule`, `rule-conflict`, `empty-dir`), правила серьёзности, кеш прогона и `--fix`.
- `agctx/cli.py` — интерфейс, вывод, коды возврата.
- `tests/` — unittest, без внешних зависимостей.

## Правила

- Новую проверку добавлять вместе с тестом в `tests/test_checks.py`.
- Запускать `python3 -m unittest discover -s tests -t .` перед коммитом.
- Ложные срабатывания чинить правилом серьёзности, а не расширением списка исключений.
- Коды возврата: `0` чисто, `1` предупреждения, `2` ошибки, `3` нет пути.

## Команды

- `python3 -m agctx scan .` — проверить текущий каталог.
- `python3 -m agctx scan ~/.dsh --no-info` — корпус скиллов.
