CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; 
CREATE TABLE users (
id SERIAL PRIMARY KEY,
user_id UUID DEFAULT uuid_generate_v4() UNIQUE,
email VARCHAR(255) UNIQUE NOT NULL,
password TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE accounts(
id SERIAL PRIMARY KEY,
user_id UUID REFERENCES users(user_id),
name TEXT NOT NULL,
type TEXT NOT NULL,
balance INTEGER default 0
);
CREATE TABLE category(
id SERIAL PRIMARY KEY,
category text NOT NULL
);
CREATE TABLE transactions(
id SERIAL PRIMARY KEY,
account_id INTEGER REFERENCES accounts(id),
ammount INTEGER NOT NULL,
type text NOT NULL,
category_id INTEGER REFERENCES category(id),
description text,
date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE budgets(
id SERIAL PRIMARY KEY,
user_id UUID REFERENCES users(user_id),
category_id INTEGER REFERENCES category(id),
month TIMESTAMP DEFAULT CURRENT_DATE,
limit_amount INTEGER NOT NULL,
spent_ammount INTEGER NOT NULL
);