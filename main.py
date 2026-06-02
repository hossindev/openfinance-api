from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from typing import Literal
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
    type: Literal["income", "expense"]
    description: str
    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls,v):
        if v <=0:
            raise ValueError("Amount must be greater than 0")
        return v

class Budget(BaseModel):
    limit_amount:int
    id:str



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
        
        
        
        async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT * FROM users WHERE email = $1",email
                )
                if existing:
                    return{"error":"user already exists"}
                user = await conn.fetchrow(
                    "INSERT INTO users (email,password) VALUES ($1,$2) RETURNING *",email , hashed.decode("utf-8")
                )   
                
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

        

        
        async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE email = $1", email
                )
                user = None
                if row:
                    user = serialize_record(dict(row))
                    

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
        raise HTTPException(status_code=500, detail=str(e))

#__________________________________________
#GET ACCOUNT
#__________________________________________
@app.get("/me")
async def get_me(user_id: str = Depends(get_current_user)):
    try:
        cached = r.get(f"user_dashboard:{user_id}")
        if cached:
            return json.loads(cached)

        async with pool.acquire() as conn:
            accounts = await conn.fetch(
                "SELECT * FROM accounts WHERE user_id = $1", user_id
            )

            if not accounts:
                return []

            account_ids = [acc["id"] for acc in accounts]

            transactions = await conn.fetch(
                "SELECT * FROM transactions WHERE account_id = ANY($1)",
                account_ids
            )

            budgets = await conn.fetch(
                "SELECT * FROM budgets WHERE account_id = ANY($1)",
                account_ids
            )

        # Group transactions and budgets by account_id for fast lookup
        tx_by_account: dict[str, list] = {}
        for tx in transactions:
            tx = serialize_record(dict(tx))
            tx_by_account.setdefault(tx["account_id"], []).append(tx)

        budget_by_account: dict[str, dict] = {}
        for b in budgets:
            b = serialize_record(dict(b))
            budget_by_account[b["account_id"]] = b

        result = []
        for acc in accounts:
            acc = serialize_record(dict(acc))
            acc_id = acc["id"]
            acc["transactions"] = tx_by_account.get(acc_id, [])
            acc["budget"] = budget_by_account.get(acc_id, None)
            result.append(acc)

        r.set(f"user_dashboard:{user_id}", json.dumps(result), ex=300)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
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
                if transaction_type == "expense":
                    row = await conn.fetchrow(
            "SELECT balance FROM accounts WHERE id = $1 AND user_id = $2",
            account_id, user_id
        )
                    if not row:
                        raise HTTPException(status_code=404, detail="Account not found")
                    if row["balance"] < amount:
                        raise HTTPException(status_code=400, detail="Insufficient funds")

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
                if operator == "-":
                    update_budget = await conn.execute("""
    UPDATE budgets
    SET spent_ammount = spent_ammount + $1
    WHERE account_id = $2
""", amount, account_id)

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

@app.get("/transactions")
async def get_transactions(user_id: str = Depends(get_current_user)):
    try:
        async with pool.acquire() as conn:
            accounts_data = await conn.fetch(
                "SELECT id FROM accounts WHERE user_id=$1", user_id
            )
            account_ids = [row["id"] for row in accounts_data]

            if not account_ids:
                return []

            transactions = await conn.fetch(
                "SELECT * FROM transactions WHERE account_id = ANY($1)",
                account_ids
            )
            return [serialize_record(dict(tx)) for tx in transactions]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/budget")
async def create_budget(data:Budget,user_id: str = Depends(get_current_user)):
    try:
        async with pool.acquire() as conn:
            budget_insert = await conn.fetch("INSERT INTO budgets (account_id,limit_amount) VALUES($1,$2) RETURNING *",data.id,data.limit_amount)
        return True
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))