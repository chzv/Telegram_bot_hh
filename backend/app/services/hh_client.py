# app/services/hh_client.py
import os
import httpx

HH_API = os.getenv("HH_API_BASE", "https://api.hh.ru")
UA = os.getenv("HH_USER_AGENT", "hhbot/1.0")


class HHError(Exception):
    """Ретраибельная ошибка (сеть/429/5xx/неясная 4xx)."""
    pass


class HHUnauthorized(HHError):
    """401 — нужна переавторизация/рефреш токена."""
    pass


class HHAlreadyApplied(Exception):
    """Уже откликались — считаем успехом."""
    pass


class HHNonRetryable(Exception):
    """Неретраибельная бизнес-ошибка (например vacancy_not_found)."""
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _parse_err(resp: httpx.Response) -> tuple[str, str]:
    """Вернёт (code, human) из тела ответа HH."""
    try:
        j = resp.json()
    except Exception:
        return "", resp.text

    code = ""
    if isinstance(j, dict):
        if isinstance(j.get("errors"), list) and j["errors"]:
            e = j["errors"][0]
            code = str(e.get("value") or e.get("type") or "")
        elif isinstance(j.get("bad_arguments"), list) and j["bad_arguments"]:
            code = str(j["bad_arguments"][0].get("name") or "")
    human = j.get("description") if isinstance(j, dict) else None
    return (code or "").strip(), (human or resp.text)


async def send_response(
    access_token: str,
    vacancy_id: int,
    resume_id: str,
    cover_letter: str | None,
):
    """
    Успех -> return.
    already_applied -> HHAlreadyApplied.
    401 -> HHUnauthorized.
    vacancy_not_found/resume_not_found -> HHNonRetryable.
    429/5xx/сеть -> HHError.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": UA,
        "HH-User-Agent": UA,
        "Accept": "application/json",
    }

    form = {"vacancy_id": str(vacancy_id), "resume_id": str(resume_id)}
    msg = (cover_letter or "").strip()
    if msg:
        form["message"] = msg

    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            r = await client.post(f"{HH_API}/negotiations", data=form)
            if r.status_code in (200, 201, 202, 204):
                return
            if r.status_code == 401:
                raise HHUnauthorized(f"401 unauthorized; body={r.text}")

            code, human = _parse_err(r)
            if code in {"already_applied", "already_negotiated"} or "Already applied" in human:
                raise HHAlreadyApplied(human)
            if code in {"vacancy_not_found", "resume_not_found"} or "Vacancy not found" in human:
                raise HHNonRetryable(f"{r.status_code}/{human}", code=code)

            # запасной эндпоинт
            alt = {"resume_id": str(resume_id)}
            if msg:
                alt["message"] = msg
            r2 = await client.post(f"{HH_API}/vacancies/{vacancy_id}/negotiations", data=alt)
            if r2.status_code in (200, 201, 202, 204):
                return
            if r2.status_code == 401:
                raise HHUnauthorized(f"401 unauthorized (alt); body={r2.text}")

            code2, human2 = _parse_err(r2)
            if code2 in {"already_applied", "already_negotiated"} or "Already applied" in human2:
                raise HHAlreadyApplied(human2)
            if code2 in {"vacancy_not_found", "resume_not_found"} or "Vacancy not found" in human2:
                raise HHNonRetryable(f"{r2.status_code}/{human2}", code=code2)

            if r.status_code in (429,) or r.status_code >= 500 or r2.status_code in (429,) or r2.status_code >= 500:
                raise HHError(f"rate/server: main {r.status_code}, alt {r2.status_code}")

            raise HHError(f"HH negotiate failed: {r.status_code}/{r.text} | alt {r2.status_code}/{r2.text}")

    except httpx.RequestError as e:
        raise HHError(f"httpx: {e!s}") from e

async def get_vacancy(access_token: str, vacancy_id: int) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": UA,
        "HH-User-Agent": UA,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        r = await client.get(f"{HH_API}/vacancies/{vacancy_id}")
        if r.status_code == 200:
            return r.json()
        raise HHError(f"vacancy_fetch {r.status_code}/{r.text}")