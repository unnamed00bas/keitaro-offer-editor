# Keitaro offer editor

Обёртка над [Keitaro](https://keitaro.io) из двух частей:

1. **Создаватор кампаний.** Название, гео (один или несколько ISO-кодов: `AU` или `AU,MX`) и оффер с поиском по ID/названию.
   Сервис создаёт в Keitaro кампанию `test_pogodina_<название>` (тип `position`) с доменом, группой и источником из настроек и двумя потоками:
   * **поток 1** (`regular`, `redirect`): фильтр «страна = выбранные гео» → `https://google.com`;
   * **поток 2** (`default`, `landings`): все остальные → выбранный оффер, 100%.
2. **Редактор офферов существующей кампании** (повторяет логику эталонного редактора из видео):
   * **Fetch streams from KT** загружает потоки кампании. Поток без офферов (Google) показывается одной строкой, без разбора фильтров и редиректа.
   * **View in KT** открывает кампанию в админке Keitaro.
   * **Add**: оффер ищется автокомплитом (наш API → Keitaro), доли пересчитываются поровну целыми процентами (3 оффера → 33/33/34). Поток становится жёлтым: это черновик, в Keitaro ещё ничего не ушло.
   * **Remove**: мягкое удаление. Оффер сереет и получает 0%, остальные делят 100%. После публикации оффер пропадает из Keitaro, а у нас остаётся в архиве с кнопкой **Bring back**.
   * **📌 Pin**: закреплённая доля не меняется при добавлении/удалении/возврате офферов, остальные делят остаток так, чтобы сумма была 100%. Клик по проценту позволяет задать долю вручную (она сразу закрепляется).
   * **Push to KT** отправляет черновик в Keitaro (`PUT /streams/{id}` со списком офферов). **Cancel** откатывает черновик к опубликованному состоянию, добавленные в черновике офферы исчезают.
   * Если оффер удалили прямо в Keitaro, при следующем Fetch он остаётся у нас и помечается удалённым (серый, «удалён в KT», есть Bring back). Офферы, добавленные в Keitaro руками, при Fetch подтягиваются.

Черновики, архив удалённых офферов и закрепления хранятся в своей БД (SQLAlchemy + Alembic). Keitaro остаётся источником правды для опубликованного состояния.

![Черновик](docs/editor-draft.png)
![После публикации: архив и закреплённая доля](docs/editor-archive.png)

## Стек

FastAPI, SQLAlchemy 2, Alembic, httpx; фронт — одна HTML-страница на чистом JS, которую раздаёт FastAPI. БД по умолчанию SQLite, Postgres через `DATABASE_URL`.

## Настройка

```bash
cp .env.example .env
# впишите KEITARO_API_KEY: Keitaro → Профиль → API ключи → Создать
```

| Переменная | По умолчанию | Что это |
|---|---|---|
| `KEITARO_ADMIN_URL` | `https://tlgk.host/admin` | адрес админки; API берётся как `<адрес>_api/v1` |
| `KEITARO_API_KEY` | — | ключ Admin API (заголовок `Api-Key`) |
| `CAMPAIGN_PREFIX` | `test_pogodina_` | префикс названий кампаний; в списке редактора показываются только кампании с ним |
| `KT_DOMAIN_ID` | `1813` | домен (`kylovoa.com`) |
| `KT_GROUP_ID` | `10332` | группа кампаний (`FORTESTS`) |
| `KT_TRAFFIC_SOURCE_ID` | `836` | источник трафика (`FORTESTS`) |
| `GOOGLE_URL` | `https://google.com` | куда отправляет первый поток |
| `DATABASE_URL` | `sqlite:///./editor.db` | строка подключения SQLAlchemy, например `postgresql+psycopg://user:pass@host:5432/db` |

## Запуск локально

Нужен Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

alembic upgrade head          # миграции: создаёт таблицы в DATABASE_URL
uvicorn app.main:app --reload # http://127.0.0.1:8000
```

## Запуск в Docker (с Postgres)

```bash
cp .env.example .env   # и впишите ключ
docker compose up --build
# http://127.0.0.1:8000
```

Контейнер приложения сам выполняет `alembic upgrade head` перед стартом.

## Миграции

```bash
alembic upgrade head                              # применить все
alembic downgrade -1                              # откатить последнюю
alembic revision --autogenerate -m "описание"     # новая миграция после правки app/models.py
```

Alembic берёт URL базы из `DATABASE_URL` (см. `alembic/env.py`).

## Тесты

```bash
pytest
```

Тесты работают с фейковым клиентом Keitaro в памяти (`tests/conftest.py`). Покрыты пересчёт долей (33/33/34, закрепления, сумма 100), черновик/отмена/публикация, мягкое удаление и возврат, пометка офферов, удалённых в Keitaro, и сквозной сценарий через HTTP API.

## API

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/offers?q=` | поиск офферов Keitaro для автокомплита |
| GET | `/api/campaigns` | кампании с префиксом из Keitaro |
| POST | `/api/campaigns` | создать кампанию `{name, geo, offer_id}` |
| GET | `/api/campaigns/{kt_id}` | состояние кампании в редакторе |
| POST | `/api/campaigns/{kt_id}/fetch` | Fetch streams from KT |
| POST | `/api/streams/{id}/offers` | добавить оффер `{offer_id}` |
| POST | `/api/streams/{id}/offers/{offer_id}/remove` | удалить (мягко) |
| POST | `/api/streams/{id}/offers/{offer_id}/bring-back` | вернуть из архива |
| POST | `/api/streams/{id}/offers/{offer_id}/pin` | `{pinned, share?}` закрепить/открепить долю |
| POST | `/api/streams/{id}/push` | опубликовать черновик в Keitaro |
| POST | `/api/streams/{id}/cancel` | отменить черновик |

Swagger: `/docs`.

## Структура

```
app/
  main.py       роуты FastAPI
  services.py   создание кампании, fetch/синхронизация, черновик, push
  shares.py     пересчёт долей
  keitaro.py    клиент Admin API Keitaro
  models.py     Campaign, Stream, StreamOffer (черновик + опубликованное состояние)
  static/       фронт
alembic/        миграции
tests/
```

## Что проверено вживую

На тестовом Keitaro (v11.5) созданы и прогнаны кампании `test_pogodina_demo1` (#213342) и `test_pogodina_demo2` (#213344): создание с двумя потоками, добавление офферов (33/33/34), push, cancel, remove с архивом, bring back, pin 25%, удаление оффера прямо в Keitaro с последующим fetch.

## Ограничения

* Авторизации у самой обёртки нет: её стоит держать во внутренней сети.
* Push перезаписывает список офферов потока целиком. Если кто-то поменял поток в Keitaro между fetch и push, его правка затрётся; перед работой лучше нажать Fetch.
* Fetch сбрасывает неопубликованный черновик (интерфейс спрашивает подтверждение).
* Колонок Stats/Trends и кнопки Show all offers из эталонного редактора нет.
