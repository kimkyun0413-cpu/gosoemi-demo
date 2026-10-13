// Local PostgreSQL/WASM only; no network, real accounts or production connection.
// npm install --no-save @electric-sql/pglite@0.5.9; node supabase/tests/ai_usage_test.mjs
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
const { PGlite } = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const dir = await mkdtemp(join(tmpdir(), 'gosomi-ai-sql-'));
let db = new PGlite(dir), checks = 0;
const migration = await readFile(new URL('../migrations/20261014000000_ai_usage.sql', import.meta.url), 'utf8');
const rollback = await readFile(new URL('../rollbacks/20261014000000_ai_usage_rollback.sql', import.meta.url), 'utf8');
const a=randomUUID(), b=randomUUID();
async function scalar(sql, args=[]) { return Object.values((await db.query(sql,args)).rows[0])[0]; }
function check(name, ok) { assert.ok(ok,name); checks++; console.log(`PASS ${name}`); }
async function denied(sql) { try { await db.exec(sql); return false; } catch { return true; } }
const reserve = (user,id=randomUUID(), limits=[30,300,100,2000]) => scalar(
 'select public.reserve_ai_usage($1,$2,$3,$4,$5,$6,$7,$8)', [user,id,'draft','gemini-3.5-flash-lite',...limits]);
try {
 await db.exec(`create role anon; create role authenticated; create role service_role;
  create schema auth; create table auth.users(id uuid primary key);
  create function auth.uid() returns uuid language sql stable as $$
    select nullif(current_setting('request.jwt.claims',true)::json->>'sub','')::uuid $$;
  grant usage on schema public to anon,authenticated,service_role;
  grant usage on schema auth to authenticated;
  insert into auth.users values('${a}'),('${b}');`);
 await db.exec(migration);
 check('migration applies', await scalar("select to_regclass('ai_private.requests') is not null"));
 await db.exec('set role authenticated');
 check('ordinary users cannot read ledger',await denied('select * from ai_private.requests'));
 check('ordinary users cannot reserve/forge usage',await denied(`select public.reserve_ai_usage('${a}','${randomUUID()}','draft','m',1,1,1,1)`));
 check('ordinary users cannot finish usage',await denied(`select public.finish_ai_usage('${randomUUID()}','succeeded',1,1,1,3)`));
 await db.exec('reset role; set role anon');
 check('anonymous export forbidden',await denied('select public.my_ai_usage()'));
 await db.exec('reset role; set role service_role');
 const id=randomUUID();
 check('server reservation', await reserve(a,id,[1,2,100,2000])==='reserved');
 check('per-user hourly limit',await reserve(a,randomUUID(),[1,2,100,2000])==='user_hour');
 check('other account independent',await reserve(b,randomUUID(),[1,2,100,2000])==='reserved');
 check('global hourly limit',await reserve(b,randomUUID(),[30,2,100,2000])==='total_hour');
 check('same request cannot be sent twice',await denied(`select public.reserve_ai_usage('${a}','${id}','draft','m',30,300,100,2000)`));
 check('finish records tokens',await scalar('select public.finish_ai_usage($1,$2,$3,$4,$5,$6)',[id,'succeeded',100,50,20,170]));
 check('finished usage cannot be overwritten',!await scalar('select public.finish_ai_usage($1,$2,$3,$4,$5,$6)',[id,'failed',0,0,0,0]));
 await db.exec('reset role');
 check('token values preserved',await scalar('select total_tokens from ai_private.requests where request_id=$1',[id])===170);
 await db.exec(`set role authenticated; select set_config('request.jwt.claims','{"sub":"${a}"}',false);`);
 const own=await scalar('select public.my_ai_usage()');
 check('export only own usage',own.length===1 && own.every(r=>r.user_id===a));
 await db.exec('reset role');
 await db.exec("update ai_private.requests set started_at=clock_timestamp()-interval '2 hours'");
 await db.exec('set role service_role');
 check('daily per-user limit',await reserve(a,randomUUID(),[30,300,1,2000])==='user_day');
 check('daily total limit',await reserve(b,randomUUID(),[30,300,100,2])==='total_day');
 const report=await scalar('select public.ai_usage_report()');
 check('operator report contains no account identifiers',!JSON.stringify(report).includes(a)&&!JSON.stringify(report).includes(b));
 await db.exec('reset role');
 await db.close(); db=new PGlite(dir);
 check('restart keeps reservations',await scalar('select count(*)::int from ai_private.requests')===2);
 await db.exec('set role service_role');
 check('restart does not reset daily limit',await reserve(a,randomUUID(),[30,300,1,2000])==='user_day');
 await db.exec('reset role; delete from ai_private.requests; set role service_role');
 const answers=await Promise.all(Array.from({length:20},()=>reserve(a,randomUUID(),[5,50,100,2000])));
 check('queued concurrent requests grant exactly five',answers.filter(x=>x==='reserved').length===5);
 await db.exec('reset role');
 const columns=(await db.query("select column_name from information_schema.columns where table_schema='ai_private' and table_name='requests'")).rows.map(r=>r.column_name);
 check('no content/key columns',!columns.some(c=>/prompt|answer|audio|secret|password/.test(c)));
 await db.exec(`delete from auth.users where id='${a}'`);
 check('account deletion removes usage',await scalar('select count(*)::int from ai_private.requests')===0);
 await db.exec(`insert into auth.users values('${a}'); set role service_role`);
 await reserve(a); await db.exec('reset role');
 await db.exec("update ai_private.requests set started_at=clock_timestamp()-interval '31 days'; set role service_role");
 check('30-day retention purge',await scalar('select public.prune_ai_usage()')===1);
 await db.exec('reset role');
 await db.exec(rollback);
 check('rollback removes ledger only',await scalar("select to_regclass('ai_private.requests') is null")&&await scalar('select count(*)::int from auth.users')===2);
 await db.exec(migration);
 check('migration re-applies after rollback',await scalar("select to_regclass('ai_private.requests') is not null"));
 // Existing draft schema + new atomic save RPC, with actual RLS permissions.
 const ca=randomUUID(),cb=randomUUID();
 await db.exec(`create table public.cases(case_id uuid primary key,user_id uuid references auth.users);
   insert into public.cases values('${ca}','${a}'),('${cb}','${b}');
   alter table public.cases enable row level security;
   grant select on public.cases to authenticated;
   create policy own_case on public.cases for select to authenticated using(user_id=auth.uid());`);
 await db.exec(await readFile(new URL('../migrations/20261006000000_case_drafts.sql', import.meta.url),'utf8'));
 await db.exec(await readFile(new URL('../migrations/20261014000001_atomic_draft_save.sql', import.meta.url),'utf8'));
 await db.exec(`set role authenticated; select set_config('request.jwt.claims','{"sub":"${a}"}',false);`);
 const save=async(text,expected=null)=> (await db.query('select r.*,r.updated_at::text as expected from public.save_case_draft_checked($1,$2,$3,true) r',[ca,text,expected])).rows;
 let first=await save('fictional draft');
 check('atomic first save',first.length===1);
 check('first-save collision returns no rows',(await save('overwrite without confirmation')).length===0);
 const updated=await save('new fictional draft',first[0].expected);
 check('atomic compare-and-save',updated.length===1);
 check('stale timestamp cannot overwrite',(await save('stale edit',first[0].expected)).length===0);
 check('other owner case cannot receive a draft',await denied(`select * from public.save_case_draft_checked('${cb}','bad',null,true)`));
 await db.exec('reset role; set role anon');
 check('anonymous atomic save forbidden',await denied(`select * from public.save_case_draft_checked('${ca}','bad',null,true)`));
 await db.exec('reset role');
 await db.exec(await readFile(new URL('../rollbacks/20261014000001_atomic_draft_save_rollback.sql',import.meta.url),'utf8'));
 check('atomic RPC rollback preserves saved draft',await scalar('select draft_text from public.case_drafts where case_id=$1',[ca])==='new fictional draft');
 console.log(`${checks}/${checks} local PostgreSQL/WASM checks passed. Native multi-server and Supabase verification still required.`);
} finally { await db.close(); await rm(dir,{recursive:true,force:true}); }
