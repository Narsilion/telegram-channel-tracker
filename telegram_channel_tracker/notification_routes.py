from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from telegram_channel_tracker.notification_setup import NotificationSetup, SetupError


def notification_router(setup: NotificationSetup) -> APIRouter:
    router = APIRouter(prefix='/api/notifications')

    @router.get('')
    async def status():
        return JSONResponse(setup.status(), headers={'Cache-Control': 'no-store'})

    @router.post('/{action:path}')
    async def action(action: str, request: Request):
        origin = request.headers.get('origin', '')
        expected = urlsplit(str(request.base_url))
        supplied = urlsplit(origin)
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc) or supplied.path not in ('', '/'):
            raise HTTPException(403, 'Setup requests must come from this application.')
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise HTTPException(415, 'Use a JSON request.')
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(413, 'Setup request is too large.')
        try:
            import json
            payload = json.loads(body)
            if not isinstance(payload, dict) or any(not isinstance(v, str) for v in payload.values()):
                raise ValueError()
        except Exception:
            raise HTTPException(400, 'Invalid setup request.') from None
        fields = {
            'email/setup': {'sender', 'recipient', 'password'}, 'email/test': set(),
            'bot/connect': {'token'}, 'bot/complete': {'connection_id'}, 'bot/test': set(),
        }
        if action not in fields:
            raise HTTPException(404, 'Unknown setup action.')
        if set(payload) - fields[action]:
            raise HTTPException(400, 'Invalid setup fields.')
        try:
            async with setup.lock:
                if action == 'email/setup':
                    result = await setup.save_email(payload.get('sender', ''), payload.get('recipient', ''), payload.get('password', ''))
                elif action == 'email/test':
                    await setup.test_email()
                    result = {'ok': True}
                elif action == 'bot/connect':
                    result = await setup.begin_bot(payload.get('token', ''))
                elif action == 'bot/complete':
                    result = await setup.finish_bot(payload.get('connection_id', ''))
                else:
                    await setup.test_bot()
                    result = {'ok': True}
        except SetupError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=400, headers={'Cache-Control': 'no-store'})
        except Exception:
            return JSONResponse({'detail': 'Setup failed. Try again; check your connection and Keychain access.'}, status_code=500,
                                headers={'Cache-Control': 'no-store'})
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    return router
