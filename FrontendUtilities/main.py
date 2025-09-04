import asyncio
import logging
import os
import subprocess
Zimport uvicorn
import py_eureka_client.eureka_client as eureka_client

from base64 import b64decode
from dotenv import load_dotenv
from fastapi import Depends, Query
from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError, ExpiredSignatureError
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Вывод логов в консоль
        # Можно добавить и другие обработчики, например, для записи в файл:
        # logging.FileHandler("app.log")
    ]
)

load_dotenv()
app = FastAPI()

GITLAB_SECRET_TOKEN = os.getenv('GITLAB_SECRET_TOKEN')
JWT_SECRET = os.getenv('JWT_SECRET')
ALGORITHM = "HS512"

security = HTTPBearer()


def register_in_eureka():
    try:
        eureka_server = os.getenv("EUREKA_SERVER")
        app_name = os.getenv("EUREKA_APP_NAME")
        instance_port = int(os.getenv("EUREKA_APP_PORT"))

        eureka_client.init(eureka_server=eureka_server,
                           app_name=app_name,
                           instance_port=instance_port)
        logging.info(f"Успешная регистрация в Eureka. Сервер: {eureka_server}, Приложение: {app_name}")
    except Exception as e:
        logging.exception("Ошибка при регистрации в Eureka.")


class Repository(BaseModel):
    name: str


class WebhookPayload(BaseModel):
    repository: Repository


async def run_update_script(script_name):
    command = f'bash ./{script_name} -f'
    logging.info(f"Запуск команды: {command}")
    stdout, stderr = await run_command(command)

    if stderr:
        logging.error(f"Ошибка при выполнении скрипта {script_name}: {stderr}")
        raise HTTPException(status_code=500, detail=f"Error: {stderr}")

    logging.info(f"Результат выполнения скрипта {script_name}: {stdout}")
    if "Успех" in stdout:
        return {"success": True}
    else:
        return {"success": False}


async def run_command(command: str):
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    return stdout.decode(), stderr.decode()


def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        secret_key = b64decode(os.getenv("JWT_SECRET"))

        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])

        return payload
    except ExpiredSignatureError:
        logging.warning("Попытка доступа с просроченным токеном.")
        raise HTTPException(status_code=403, detail="Token has expired")
    except JWTError as e:
        logging.error(f"Ошибка валидации JWT токена: {e}")
        raise HTTPException(status_code=403, detail=f"Invalid JWT token: {str(e)}")
    except Exception:
        logging.exception("Неизвестная ошибка при декодировании JWT токена.")
        raise HTTPException(status_code=403, detail="Could not validate credentials")


@app.post("/gitlab-update-container")
async def gitlab_update_container(request: Request):
    gitlab_token = request.headers.get("X-Gitlab-Token")
    if gitlab_token != GITLAB_SECRET_TOKEN:
        logging.warning("Неудачная попытка доступа к веб-хуку GitLab: неверный токен.")
        raise HTTPException(status_code=403, detail="Forbidden: Invalid GitLab token")

    try:
        data = await request.json()
    except Exception:
        logging.error("Не удалось разобрать JSON из тела запроса веб-хука GitLab.")
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    repository = data.get("repository")
    if not repository or not isinstance(repository, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'repository' object")

    repo_name = repository.get("name")
    if not repo_name:
        raise HTTPException(status_code=400, detail="Missing 'name' in repository")

    logging.info(f"Получен веб-хук от GitLab для репозитория: {repo_name}")

    if repo_name == "Lectoria Frontend":
        asyncio.create_task(run_update_script("test.sh"))
    elif repo_name == "Lectoria Mobile":
        asyncio.create_task(run_update_script("testMobile.sh"))
    else:
        logging.warning(f"Получен веб-хук для неизвестного репозитория: {repo_name}")
        raise HTTPException(status_code=400,
                            detail=f"Invalid repository name: {repo_name}. Expected 'Lectoria Frontend' or 'Lectoria Mobile'")

    return {"message": "ok"}


@app.post("/update-container")
async def update_container(
        type: str = Query(..., description="Specify 'desktop' or 'mobile' to determine which script to run"),
        payload: dict = Depends(verify_jwt_token)
):
    user_roles = payload.get("roles")
    if user_roles != "ADMIN":
        logging.warning(f"Пользователь с ролями '{user_roles}' попытался обновить контейнер, не имея прав ADMIN.")
        raise HTTPException(status_code=403, detail="Forbidden: You do not have the required role")

    logging.info(f"Администратор инициировал обновление контейнера типа: {type}")

    if type == "desktop":
        asyncio.create_task(run_update_script("test.sh"))
    elif type == "mobile":
        asyncio.create_task(run_update_script("testMobile.sh"))
    else:
        logging.error(f"Получен неверный тип для обновления: {type}")
        raise HTTPException(status_code=400, detail="Invalid type. Expected 'desktop' or 'mobile'.")

    return {"message": "ok"}


if __name__ == "__main__":
    register_in_eureka()
    uvicorn.run(app, host="0.0.0.0", port=12721)
