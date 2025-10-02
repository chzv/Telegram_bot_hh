# file: services/autoresponder.py
from sqlalchemy import text
from sqlalchemy.engine import Engine
from app.services.limits import quota_for_user

def plan_autoresponses(engine: Engine, hh_search):
    """
    engine   — SQLAlchemy Engine
    hh_search(query_params: str) -> list[dict]  (каждый dict с ключом 'id')
    """
    total_added = 0
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT r.id AS run_id, r.taken, r.sent,
                   ar.id AS auto_id, ar.user_id, ar.resume_id, ar.daily_limit,
                   sr.query_params, COALESCE(sr.cover_letter, '') AS cover_letter
              FROM auto_runs r
              JOIN auto_responses ar ON ar.id = r.auto_id
              JOIN saved_requests sr ON sr.id = ar.saved_request_id
             WHERE r.d = CURRENT_DATE
               AND ar.active = TRUE
        """)).mappings().all()

        for row in rows:
            # 1) Квота пользователя на сегодня
            q = quota_for_user(conn, user_id=row["user_id"])
            allowed_user = max(0, q["remaining"])

            # 2) Остаток по конкретному авто-запуску (daily_limit - taken)
            remaining_run = max(0, int(row["daily_limit"]) - int(row["taken"]))

            # 3) Итоговый лимит для планирования сейчас
            allowed = min(allowed_user, remaining_run)
            if allowed <= 0:
                # фиксируем, что «упёрлись» в лимит
                conn.execute(text("""
                    UPDATE auto_runs
                       SET queued = 0
                     WHERE auto_id = :aid AND d = CURRENT_DATE
                """), {"aid": row["auto_id"]})
                continue

            # 4) Ищем свежие вакансии
            vacancies = hh_search(row["query_params"]) or []
            if not vacancies:
                continue

            vacancy_ids = [str(v["id"]) for v in vacancies]

            # 5) Отбрасываем уже имеющиеся заявки
            existing = conn.execute(text("""
                SELECT vacancy_id
                  FROM applications
                 WHERE user_id = :uid
                   AND vacancy_id = ANY(:vids::text[])
            """), {"uid": row["user_id"], "vids": vacancy_ids}).scalars().all()
            existing_set = set(str(x) for x in existing)

            to_queue = [vid for vid in vacancy_ids if vid not in existing_set][:allowed]
            if not to_queue:
                continue

            # 6) Массовая вставка
            inserted = conn.execute(text("""
                INSERT INTO applications (user_id, vacancy_id, cover_letter,
                                          status, kind, resume_id, answers)
                SELECT :uid, v.vid, :cover, 'queued', 'auto', :resume_id, '[]'::jsonb
                  FROM UNNEST(:vids::text[]) AS v(vid)
                ON CONFLICT ON CONSTRAINT uq_applications_user_vacancy DO NOTHING
                RETURNING id
            """), {
                "uid": row["user_id"],
                "vids": to_queue,
                "cover": row["cover_letter"],
                "resume_id": row["resume_id"],
            }).rowcount or 0

            if inserted > 0:
                conn.execute(text("""
                    UPDATE auto_runs
                       SET taken = taken + :n
                     WHERE auto_id = :auto_id
                       AND d = (now() AT TIME ZONE 'UTC')::date
                """), {"n": inserted, "auto_id": row["auto_id"]})
                total_added += inserted

    return total_added
