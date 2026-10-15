import logging

from core.supabase import admin, get_current_user
from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)

router = APIRouter(tags=["User"])


@router.get("/me", status_code=200)
def me(user=Depends(get_current_user)):
    profile = None
    try:
        res = admin.table("profiles").select("*").eq("id", user.id).execute()
        if res and res.data:
            profile = res.data[0]
    except Exception as exc:
        logger.warning("Could not fetch user profile for %s: %s", user.id, exc)

    return {
        "id": user.id,
        "email": user.email,
        "profile": profile,
    }
