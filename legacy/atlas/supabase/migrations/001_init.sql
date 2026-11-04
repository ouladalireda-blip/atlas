-- ATLAS — schéma Supabase (Postgres + pgvector)
-- Couche 2 de la mémoire : le registre exact (sessions, messages, actions, vérifications)
-- + couche 1 (vecteurs) + couche 3 (faits durables) + données des outils.

create extension if not exists vector;

create table if not exists sessions (
  id          text primary key,
  title       text,
  created_at  timestamptz not null default now()
);

-- Chaque échange, mot pour mot.
create table if not exists messages (
  id          bigserial primary key,
  session_id  text not null references sessions(id) on delete cascade,
  turn_id     text not null,
  role        text not null check (role in ('user','assistant')),
  content     text not null,
  created_at  timestamptz not null default now()
);
create index if not exists messages_session_idx on messages(session_id, id);

-- Chaque appel d'outil = un reçu. C'est la "vérité terrain" du vérificateur.
create table if not exists actions (
  id          bigserial primary key,
  session_id  text not null,
  turn_id     text not null,
  tool        text not null,
  effect      text,                       -- ex : calendar.write, email.send
  args        jsonb not null default '{}',
  result      jsonb,
  status      text not null,              -- ok | error | pending_approval | sent
  created_at  timestamptz not null default now()
);
create index if not exists actions_turn_idx on actions(turn_id);

-- Journal du vérificateur : ce que la réponse affirmait vs ce qui a tourné.
create table if not exists verifications (
  id          bigserial primary key,
  turn_id     text not null,
  claims      jsonb not null default '[]',
  unsupported jsonb not null default '[]',
  verdict     text not null,              -- no_claims | verified | corrected | blocked
  created_at  timestamptz not null default now()
);

-- Coûts et cache, par tour.
create table if not exists turns (
  id                text primary key,
  session_id        text not null,
  effort            text,
  model             text,
  prompt_tokens     int default 0,
  completion_tokens int default 0,
  cached_tokens     int default 0,
  cost_usd          numeric(12,6) default 0,
  created_at        timestamptz not null default now()
);

-- Couche 3 : faits durables, chacun étiqueté avec qui l'a dit.
create table if not exists facts (
  id          bigserial primary key,
  content     text not null,
  speaker     text not null check (speaker in ('user','assistant')),
  session_id  text,
  message_id  bigint,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Couche 1 : index vectoriel unique sur messages, faits et notes.
create table if not exists chunks (
  id          bigserial primary key,
  kind        text not null check (kind in ('message','fact','note')),
  ref         text not null,              -- id du message / du fait, ou chemin de la note
  content     text not null,
  embedding   vector(384) not null,
  created_at  timestamptz not null default now(),
  unique (kind, ref)
);
create index if not exists chunks_embedding_idx
  on chunks using hnsw (embedding vector_cosine_ops);

-- Données des outils
create table if not exists events (
  id          bigserial primary key,
  title       text not null,
  starts_at   timestamptz not null,
  ends_at     timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists tasks (
  id          bigserial primary key,
  title       text not null,
  due         date,
  done        boolean not null default false,
  created_at  timestamptz not null default now()
);

-- E-mails : rien ne part sans approbation humaine.
create table if not exists outbox (
  id          bigserial primary key,
  turn_id     text,
  recipient   text not null,
  subject     text not null,
  body        text not null,
  status      text not null default 'pending_approval', -- pending_approval | sent | rejected
  created_at  timestamptz not null default now(),
  decided_at  timestamptz
);

-- Recherche vectorielle appelée par l'app (cosinus).
create or replace function match_chunks(query_embedding vector(384), match_count int)
returns table (kind text, ref text, content text, similarity float, created_at timestamptz)
language sql stable as $$
  select kind, ref, content, 1 - (embedding <=> query_embedding) as similarity, created_at
  from chunks
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- Usage mono-utilisateur, accès serveur uniquement (connexion Postgres directe).
-- Active RLS sans policy pour bloquer l'accès via les clés publiques.
alter table sessions enable row level security;
alter table messages enable row level security;
alter table actions enable row level security;
alter table verifications enable row level security;
alter table turns enable row level security;
alter table facts enable row level security;
alter table chunks enable row level security;
alter table events enable row level security;
alter table tasks enable row level security;
alter table outbox enable row level security;
