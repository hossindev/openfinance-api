from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
import asyncio 
import os
import redis
import asyncpg
import json
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import uuid

load_dotenv()
DATABASE_URL = f"postgresql://openfinance:{os.getenv('password')}@localhost/openfinance_db"
pool: asyncpg.pool = None
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
SECRET_KEY=os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool

    retries = 5

    for attempt in range(retries):
        try:
            pool = await asyncpg.create_pool(
    DATABASE_URL,
    timeout=10,       # War 30
    command_timeout=10
)
            print("Database connected")
            break

        except Exception as e:
            print(f"DB connection failed: {e}")

            if attempt == retries - 1:
                raise e

            await asyncio.sleep(5)

    yield

    await pool.close()
app = FastAPI(lifespan=lifespan)
security = HTTPBearer()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://finance.ryzzlab.xyz"],  # later the real url only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#__________________________________________
#HELPERS
#__________________________________________

def create_access_token(data:dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp":expire})
    return jwt.encode(to_encode,SECRET_KEY,algorithm=ALGORITHM)
async def get_current_user(credentials:HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token,SECRET_KEY,algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401,detail="Invalid token")
        return user_id
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token or {str(e)}")
    
def cache_user(email: str, user_data: dict):
    r.set(f"user:{email}", json.dumps(user_data))

def get_cached_user(email: str):
    data = r.get(f"user:{email}")
    return json.loads(data) if data else None



def serialize_record(record: dict) -> dict:
    return {
        k: str(v) if isinstance(v, (uuid.UUID, datetime)) else v 
        for k, v in record.items()
    }


#__________________________________________
#CLASSES 
#__________________________________________
class Token(BaseModel):
    access_token:str
    token_type:str

class LoginData(BaseModel):
    email:str
    password:str

class Accounts(BaseModel):
    name:str
    account_type:str

class TransactionData(BaseModel):
    account_id: str
    amount: int
    category_id:int
    type: str
    description: str


@app.get("/health")
async def health():
    return {"status": "ok"}

#__________________________________________
#SIGNUP
#__________________________________________
@app.post("/signup")
async def signup(data:LoginData):
    try:
        email = data.email
        password = data.password
        hashed = bcrypt.hashpw(password.encode("utf-8"),bcrypt.gensalt())
        
        existing = get_cached_user(email)
        if existing:
            return{"error":"user already exists"}
        else:
            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT * FROM users WHERE email = $1",email
                )
                if existing:
                    return{"error":"user already exists"}
                user = await conn.fetchrow(
                    "INSERT INTO users (email,password) VALUES ($1,$2) RETURNING *",email , hashed.decode("utf-8")
                )   
                cache_user(email, serialize_record(dict(user)))
                return {"signup": True}
    except Exception as e:
        print(f"SIGNUP ERROR: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
#__________________________________________
#LOGIN WITH JWT
#__________________________________________
@app.post("/login")
async def login(data: LoginData):
    try:
        email = data.email
        password = data.password

        user = get_cached_user(email)

        if not user:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE email = $1", email
                )
                if row:
                    user = serialize_record(dict(row))
                    cache_user(email, user)

        if not user or not bcrypt.checkpw(
            password.encode(), user["password"].encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        access_token = create_access_token(data={"sub": str(user["user_id"])})
        return {"login": True, "access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#__________________________________________
# CREATE ACCOUNt
#__________________________________________  
@app.post("/create-account")
async def create_account(data:Accounts ,user_id:str = Depends(get_current_user) ):
    try:
        name = data.name
        account_type = data.account_type
        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                "INSERT INTO accounts (user_id,name,type) VALUES ($1,$2,$3) RETURNING *", user_id,name,account_type
            )
            r.delete(f"user_dashboard:{user_id}")

        return {"sucess":True,"account_id":account["id"]}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

#__________________________________________
#GET ACCOUNT
#__________________________________________
@app.get("/me")
async def get_me(user_id:str = Depends(get_current_user)):
    try:
        cached = r.get(f"user_dashboard:{user_id}")
        if cached:
            return json.loads(cached)

        
        async with pool.acquire() as conn:
            accounts = await conn.fetch(
                "SELECT * FROM accounts WHERE user_id=$1",
                user_id
            )
            result = [serialize_record(dict(acc)) for acc in accounts]
            r.set(f"user_dashboard:{user_id}", json.dumps(result))
            return result    
    except Exception as e:
        raise HTTPException(status_code=500,detail=str(e))
    
#__________________________________________
# TRANSACTIONS
#__________________________________________
@app.post("/create-transaction")
async def create_transaction(
    data: TransactionData,
    user_id: str = Depends(get_current_user)
):
    try:
        account_id = data.account_id
        amount = data.amount
        category_id = data.category_id
        description = data.description
        transaction_type = data.type

        async with pool.acquire() as conn:

            async with conn.transaction():

                # income adds money, expense removes money
                operator = "+" if transaction_type == "income" else "-"

                update_balance = await conn.fetchrow(
                    f"""
                    UPDATE accounts
                    SET balance = balance {operator} $1
                    WHERE id = $2
                    AND user_id=$3
                    RETURNING *
                    """,
                    amount,
                    account_id,
                    user_id
                )

                if not update_balance:
                    raise HTTPException(
                        status_code=404,
                        detail="Account not found"
                    )

                new_transaction = await conn.fetchrow(
                    """
                    INSERT INTO transactions
                    (ammount, account_id, category_id, description, type)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING *
                    """,
                    amount,
                    account_id,
                    category_id,
                    description,
                    transaction_type
                )

            r.delete(f"user_dashboard:{user_id}")
            

            return {
                "success": True,
                "account": serialize_record(dict(update_balance)),
                "transaction": serialize_record(dict(new_transaction))
            }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
    



