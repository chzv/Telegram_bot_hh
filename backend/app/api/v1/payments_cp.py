# backend/app/api/v1/payments_cp.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from string import Template
import logging
from app.core.config import CP_PUBLIC_ID, PAY_RETURN_BOT_URL

from fastapi import Request
from fastapi.responses import JSONResponse
import hmac, hashlib, base64, json
from datetime import datetime, timedelta, timezone
from app.core.config import CP_API_SECRET 
from sqlalchemy import text
from app.db import SessionLocal
from sqlalchemy import select, insert, update

router = APIRouter(prefix="/pay", tags=["payments"])

PLANS = {
    "week":  {"amount": 590,  "name": "Подписка на 7 дней"},
    "month": {"amount": 1390, "name": "Подписка на 30 дней"},
}

HTML_TEMPLATE = Template("""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<meta name="theme-color" content="#0ea5e9"/>
<title>Оплата — $description</title>

<link rel="preconnect" href="https://widget.cloudpayments.ru" crossorigin>
<link rel="dns-prefetch" href="//widget.cloudpayments.ru">
<script src="https://widget.cloudpayments.ru/bundles/cloudpayments"></script>

<style>
:root{
  --bg:#0b1220;           /* фон */
  --card:#0f172a;         /* карточка */
  --text:#e5e7eb;         /* основной текст */
  --muted:#94a3b8;        /* вторичный текст */
  --brand:#0ea5e9;        /* акцент */
  --brand-contrast:#071827;
  --ok:#22c55e;
  --radius:18px;
  --shadow:0 10px 30px rgba(2,6,23,.35);
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f1f5f9; --card:#ffffff; --text:#0f172a; --muted:#475569;
    --brand:#2563eb; --brand-contrast:#eef2ff;
  }
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
  color:var(--text); background:radial-gradient(1200px 800px at 10% -10%,rgba(14,165,233,.25),transparent 60%),
                     radial-gradient(900px 600px at 100% 0,rgba(34,197,94,.18),transparent 60%),
                     var(--bg);
  display:grid; place-items:center; padding:24px;
}
.card{
  width:min(680px, 100%);
  background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
  padding:28px; position:relative; overflow:hidden;
}
.header{
  display:flex; align-items:center; gap:14px; margin-bottom:18px;
}
.badge{
  background:linear-gradient(135deg, rgba(14,165,233,.18), rgba(99,102,241,.18));
  color:var(--text); border:1px solid rgba(148,163,184,.25);
  padding:6px 10px; border-radius:999px; font-size:12px; letter-spacing:.3px;
}
h1{font-size:22px; margin:0}
.desc{color:var(--muted); margin:8px 0 22px; line-height:1.45}
.row{display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap}
.amount{
  font-size:34px; font-weight:800; letter-spacing:.5px;
  background:linear-gradient(90deg, var(--text), #a5b4fc);
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
.hint{color:var(--muted); font-size:13px}
.features{margin:14px 0 0; padding-left:18px; color:var(--muted)}
.features li{margin:6px 0}
.actions{display:flex; align-items:center; gap:12px; margin-top:20px; flex-wrap:wrap}
.btn{
  appearance:none; border:0; border-radius:12px; padding:14px 18px;
  font-size:16px; font-weight:600; cursor:pointer; transition:.2s transform ease, .2s opacity ease;
}
.btn-primary{background:var(--brand); color:#fff; box-shadow:0 6px 18px rgba(14,165,233,.35)}
.btn-primary:active{transform:translateY(1px)}
.btn:disabled{opacity:.6; cursor:not-allowed}
.sec{color:var(--muted); font-size:12px}
.spinner{
  width:64px;height:64px;border-radius:50%;
  border:6px solid rgba(148,163,184,.35); border-top-color:var(--brand);
  animation:spin 1s linear infinite; display:none; margin:26px auto 2px;
}
@keyframes spin{to{transform:rotate(360deg)}}
.footer{
  margin-top:18px; display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;
  color:var(--muted); font-size:12px;
  border-top:1px dashed rgba(148,163,184,.25); padding-top:14px;
}
.logo-dot{width:10px;height:10px;border-radius:50%; background:var(--ok); margin-right:6px; display:inline-block}
</style>
</head>
<body>
  <main class="card" role="main">
    <div class="header">
      <span class="badge">HHOFFER</span>
      <h1>Оплата: $description</h1>
    </div>

    <p class="desc">Завершаем оформление — оплата защищена CloudPayments. После успешной оплаты вернитесь в Telegram, статус обновится автоматически (или по кнопке «Обновить»).</p>

    <div class="row" aria-live="polite">
      <div class="amount">$amount ₽</div>
      <div class="hint">Разовая оплата, без автосписаний</div>
    </div>

    <ul class="features">
      <li>до 200 откликов в сутки</li>
      <li>умные паузы и фильтры</li>
      <li>приоритетная поддержка</li>
    </ul>

    <div class="actions">
      <button id="pay" class="btn btn-primary">Оплатить</button>
      <div class="sec">Поддерживаются карты Visa/Mastercard/МИР</div>
    </div>

    <div id="spinner" class="spinner" aria-hidden="true"></div>

    <div class="footer">
      <div><span class="logo-dot"></span>Соединение защищено</div>
      <div>CloudPayments • PCI DSS</div>
    </div>
  </main>

<script>
function ensureCpReady(maxTries, delayMs){
  return new Promise(function(res, rej){
    var n=0;(function tick(){
      if (window.cp && typeof window.cp.CloudPayments === 'function') return res();
      if (++n >= maxTries) return rej(new Error('cp not ready'));
      setTimeout(tick, delayMs);
    })();
  });
}
(function(){
  var btn=document.getElementById('pay');
  var sp=document.getElementById('spinner');

  btn.addEventListener('click', function(){
    btn.disabled=true; sp.style.display='block';
    ensureCpReady(30,100).then(function(){
      try{
        var widget=new cp.CloudPayments({language:'ru-RU'});
        widget.pay('charge',{
          publicId:'$public_id',
          description:'$description',
          amount:$amount,
          currency:'RUB',
          invoiceId:'$tg_id-$plan-' + Date.now(),
          accountId:'$tg_id',
          data:{tg_id:'$tg_id', plan:'$plan'}
        },{
          onSuccess:function(){ window.location.href='$success_redirect'; },
          onFail:function(reason){
            alert('Оплата не прошла: ' + (reason||'')); 
          },
          onComplete:function(){ btn.disabled=false; sp.style.display='none'; }
        });
      }catch(e){
        alert('Не удалось открыть форму оплаты. Попробуйте ещё раз.');
        btn.disabled=false; sp.style.display='none';
      }
    }).catch(function(){
      alert('Виджет оплаты не подгрузился. Обновите страницу и попробуйте снова.');
      btn.disabled=false; sp.style.display='none';
    });
  });
})();
</script>
</body>
</html>""")

@router.get("", response_class=HTMLResponse)
async def pay_page(plan: str, tg_id: int):
    try:
        if plan not in PLANS:
            raise HTTPException(400, "unknown plan")
        if not CP_PUBLIC_ID:
            raise RuntimeError("CP_PUBLIC_ID is not configured")
        if not PAY_RETURN_BOT_URL:
            raise RuntimeError("PAY_RETURN_BOT_URL is not configured")

        html = HTML_TEMPLATE.substitute(
            public_id=CP_PUBLIC_ID,
            amount=PLANS[plan]["amount"],
            description=PLANS[plan]["name"],
            plan=plan,
            tg_id=tg_id,
            success_redirect=PAY_RETURN_BOT_URL,
        )
        return Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": "inline",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "script-src 'self' https://widget.cloudpayments.ru 'unsafe-inline'; "
                    "script-src-elem 'self' https://widget.cloudpayments.ru 'unsafe-inline'; "
                    "connect-src https://widget.cloudpayments.ru https://api.cloudpayments.ru; "
                    "frame-src https://widget.cloudpayments.ru; "
                    "img-src 'self' data: https://widget.cloudpayments.ru; "
                    "style-src 'self' 'unsafe-inline'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'self' https://t.me https://telegram.org;"
                ),
            },
        )
    except Exception:
        logging.exception("pay_page failed")
        return HTMLResponse("<h2>Ошибка оплаты</h2>", status_code=500)
