# Evidence Compass Longevity

Приложение для поиска публикаций и извлечения опубликованных показателей
продолжительности жизни и возраста животных с сохранением исходных оснований.

## Запуск

Нужен Docker с Compose.

1. Создайте `.env` рядом с `docker-compose.yml` и укажите подключение к модели
   (OpenAI-совместимый API, ADR 0007):

   ```sh
   EC_LLM_API_KEY=...          # ключ; без ключа и модели критерии заполняются вручную
   EC_LLM_MODEL=...            # например, модель OpenRouter
   # EC_LLM_BASE_URL=...       # пусто — OpenRouter
   # EC_OPENROUTER_PROVIDERS=  # только OpenRouter: провайдеры через запятую
   # EC_UNPAYWALL_EMAIL=...    # без адреса открытый PDF по DOI не ищется
   ```

2. Соберите и запустите приложение (БД, API, worker, клиент). API применяет
   миграции при старте.

   ```sh
   docker compose up -d --build
   ```

3. Создайте учётную запись: регистрации нет, пароль запросит команда.

   ```sh
   docker compose exec api uv run --no-sync python -m evidence_compass.cli create-account <login>
   ```

4. Откройте <http://localhost:8080> и войдите.

Остановка — `docker compose down`; с удалением БД и документов — `docker compose down -v`.

### Разработка

```sh
docker compose up -d db                                            # рабочая БД на :5433
cd backend && uv run alembic upgrade head
uv run uvicorn evidence_compass.api:create_app --factory --reload  # API
uv run python -m evidence_compass.worker                           # worker, в отдельном терминале
cd ../frontend && pnpm install && pnpm dev                         # клиент
```

Клиент без сервера на демо-данных — `pnpm dev:mock`. Тесты API требуют
`docker compose --profile test up -d db-test`, затем `cd backend && uv run pytest`.

