CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id UUID DEFAULT uuid_generate_v4() UNIQUE,
    email VARCHAR(255) UNIQUE NOT NULL,
    password TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    balance INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS category (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    ammount INTEGER NOT NULL,
    type TEXT NOT NULL,
    category_id INTEGER REFERENCES category(id),
    description TEXT,
    date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budgets (
    id SERIAL PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    month DATE DEFAULT CURRENT_DATE,
    limit_amount INTEGER NOT NULL,
    spent_ammount INTEGER DEFAULT 0
);