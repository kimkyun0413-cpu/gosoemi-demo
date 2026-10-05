-- [제안 · 운영 DB 미적용] 유료 요금제 결제 구조
-- 결제사(PG)를 정하지 않은 공통 구조다. 사업자등록·통신판매업 신고·결제사 계약 전에는 적용하지 않는다.
--
-- 원칙
-- - 금액은 클라이언트가 아니라 billing_plans 표에서 서버가 정한다.
-- - 주문 생성은 로그인 사용자가 billing_create_order()로만 한다. 같은 멱등 키로 다시 부르면 같은 주문을 돌려준다.
-- - 결제 결과 반영은 결제사 확인을 마친 서버(Edge Function, service_role)만 billing_apply_event()로 한다.
--   같은 결제사 이벤트를 여러 번 받아도 한 번만 반영되고(billing_events), 금액이 다르면 거부한다.
-- - 이용자는 자신의 주문·이용권·구독만 조회할 수 있고 직접 수정할 수 없다.
-- - 전자상거래법 시행령 제6조: 대금결제 기록 5년 보존, 동의를 철회한 소비자의 기록은 별도 보존.
--   회원탈퇴 시 billing_retain_orders_for_user()로 결제 기록을 billing_private에 옮긴 뒤 계정을 삭제한다.
-- 되돌리기: 20261007000000_billing_rollback.sql

create schema if not exists billing_private;
revoke all on schema billing_private from public, anon, authenticated;

create table public.billing_plans (
    plan_id text primary key check (plan_id ~ '^[a-z0-9_-]{1,40}$'),
    name text not null check (char_length(name) between 1 and 60),
    kind text not null check (kind in ('one_time', 'subscription')),
    amount integer not null check (amount > 0),
    currency text not null default 'KRW' check (currency = 'KRW'),
    period_days integer not null check (period_days between 1 and 366),
    active boolean not null default false,
    created_at timestamptz not null default now()
);

create table public.billing_orders (
    order_id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    plan_id text not null references public.billing_plans (plan_id),
    amount integer not null check (amount > 0),
    currency text not null,
    status text not null default 'pending'
        check (status in ('pending', 'paid', 'failed', 'cancelled', 'refunded')),
    idempotency_key text not null check (char_length(idempotency_key) between 8 and 80),
    provider text,
    provider_payment_id text unique,
    failure_reason text check (char_length(failure_reason) <= 200),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    paid_at timestamptz,
    unique (user_id, idempotency_key)
);

create table public.billing_entitlements (
    user_id uuid not null references auth.users (id) on delete cascade,
    plan_id text not null references public.billing_plans (plan_id),
    valid_until timestamptz not null,
    source_order_id uuid references public.billing_orders (order_id) on delete set null,
    updated_at timestamptz not null default now(),
    primary key (user_id, plan_id)
);

create table public.billing_subscriptions (
    user_id uuid not null references auth.users (id) on delete cascade,
    plan_id text not null references public.billing_plans (plan_id),
    status text not null check (status in ('active', 'past_due', 'cancelled', 'expired')),
    current_period_end timestamptz not null,
    cancel_at_period_end boolean not null default false,
    provider_subscription_id text unique,
    updated_at timestamptz not null default now(),
    primary key (user_id, plan_id)
);

-- 결제사 이벤트 중복 처리 방지. 원문은 저장하지 않고 해시만 둔다.
create table public.billing_events (
    provider text not null,
    event_id text not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
    received_at timestamptz not null default now(),
    primary key (provider, event_id)
);

-- 탈퇴한 이용자의 결제 기록(5년 보존). Data API에 노출하지 않는다.
create table billing_private.retained_orders (
    order_id uuid primary key,
    plan_id text not null,
    amount integer not null,
    currency text not null,
    status text not null,
    provider text,
    provider_payment_id text,
    created_at timestamptz not null,
    paid_at timestamptz,
    retained_at timestamptz not null default now(),
    retain_until timestamptz not null
);

alter table public.billing_plans enable row level security;
alter table public.billing_orders enable row level security;
alter table public.billing_orders force row level security;
alter table public.billing_entitlements enable row level security;
alter table public.billing_entitlements force row level security;
alter table public.billing_subscriptions enable row level security;
alter table public.billing_subscriptions force row level security;
alter table public.billing_events enable row level security;
alter table billing_private.retained_orders enable row level security;

revoke all on table public.billing_plans, public.billing_orders, public.billing_entitlements,
    public.billing_subscriptions, public.billing_events from public, anon, authenticated;
revoke all on table billing_private.retained_orders from public, anon, authenticated;
grant select on table public.billing_plans, public.billing_orders, public.billing_entitlements,
    public.billing_subscriptions to authenticated;

create policy "billing plans active" on public.billing_plans
for select to authenticated using (active);
create policy "billing orders own" on public.billing_orders
for select to authenticated using ((select auth.uid()) = user_id);
create policy "billing entitlements own" on public.billing_entitlements
for select to authenticated using ((select auth.uid()) = user_id);
create policy "billing subscriptions own" on public.billing_subscriptions
for select to authenticated using ((select auth.uid()) = user_id);


-- 로그인 사용자의 주문 생성. 금액은 요금제 표에서 가져온다. 같은 멱등 키는 같은 주문을 돌려준다.
create or replace function public.billing_create_order(p_plan_id text, p_idempotency_key text)
returns public.billing_orders
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_user uuid := auth.uid();
    v_plan public.billing_plans;
    v_order public.billing_orders;
begin
    if v_user is null then
        raise exception 'billing_create_order: unauthenticated' using errcode = '42501';
    end if;
    select * into v_plan from public.billing_plans where plan_id = p_plan_id and active;
    if not found then
        raise exception 'billing_create_order: plan not available';
    end if;

    insert into public.billing_orders (user_id, plan_id, amount, currency, idempotency_key)
    values (v_user, v_plan.plan_id, v_plan.amount, v_plan.currency, p_idempotency_key)
    on conflict (user_id, idempotency_key) do nothing
    returning * into v_order;

    if v_order.order_id is null then
        select * into v_order from public.billing_orders
        where user_id = v_user and idempotency_key = p_idempotency_key;
        if v_order.plan_id <> p_plan_id then
            raise exception 'billing_create_order: idempotency key reused for another plan';
        end if;
    end if;
    return v_order;
end;
$$;


-- 결제사에서 확인한 결과를 반영한다(service_role 전용). 반환값: applied / duplicate / ignored / amount_mismatch
-- p_kind: payment_succeeded, payment_failed, payment_cancelled, refunded,
--         renewal_succeeded, renewal_failed, subscription_cancelled, subscription_expired
create or replace function public.billing_apply_event(
    p_provider text,
    p_event_id text,
    p_payload_sha256 text,
    p_kind text,
    p_order_id uuid,
    p_provider_payment_id text,
    p_amount integer,
    p_reason text default null
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_order public.billing_orders;
    v_plan public.billing_plans;
    v_until timestamptz;
begin
    insert into public.billing_events (provider, event_id, payload_sha256)
    values (p_provider, p_event_id, p_payload_sha256)
    on conflict do nothing;
    if not found then
        return 'duplicate';
    end if;

    select * into v_order from public.billing_orders where order_id = p_order_id for update;
    if not found then
        return 'ignored';
    end if;
    select * into v_plan from public.billing_plans where plan_id = v_order.plan_id;

    if p_kind in ('payment_succeeded', 'renewal_succeeded') then
        if p_amount is distinct from v_order.amount then
            update public.billing_orders
            set status = 'failed', failure_reason = 'amount_mismatch', updated_at = now()
            where order_id = v_order.order_id and status = 'pending';
            return 'amount_mismatch';
        end if;
        if p_kind = 'payment_succeeded' and v_order.status <> 'pending' then
            return 'ignored';
        end if;
        update public.billing_orders
        set status = 'paid', provider = p_provider, provider_payment_id = p_provider_payment_id,
            paid_at = coalesce(paid_at, now()), updated_at = now()
        where order_id = v_order.order_id;

        insert into public.billing_entitlements (user_id, plan_id, valid_until, source_order_id)
        values (v_order.user_id, v_plan.plan_id, now() + make_interval(days => v_plan.period_days), v_order.order_id)
        on conflict (user_id, plan_id) do update
        set valid_until = greatest(public.billing_entitlements.valid_until, now())
                          + make_interval(days => v_plan.period_days),
            source_order_id = excluded.source_order_id,
            updated_at = now()
        returning valid_until into v_until;

        if v_plan.kind = 'subscription' then
            insert into public.billing_subscriptions (user_id, plan_id, status, current_period_end)
            values (v_order.user_id, v_plan.plan_id, 'active', v_until)
            on conflict (user_id, plan_id) do update
            set status = 'active', current_period_end = excluded.current_period_end, updated_at = now();
        end if;
        return 'applied';

    elsif p_kind in ('payment_failed', 'payment_cancelled') then
        update public.billing_orders
        set status = case when p_kind = 'payment_failed' then 'failed' else 'cancelled' end,
            failure_reason = left(p_reason, 200), updated_at = now()
        where order_id = v_order.order_id and status = 'pending';
        return case when found then 'applied' else 'ignored' end;

    elsif p_kind = 'refunded' then
        update public.billing_orders set status = 'refunded', updated_at = now()
        where order_id = v_order.order_id and status = 'paid';
        if not found then
            return 'ignored';
        end if;
        -- 환불된 주문으로 받은 이용권은 즉시 끝낸다. 부분 환불 등 다른 처리는 운영 정책 확정 후 정한다.
        update public.billing_entitlements set valid_until = now(), updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id and source_order_id = v_order.order_id;
        update public.billing_subscriptions set status = 'cancelled', updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id;
        return 'applied';

    elsif p_kind = 'renewal_failed' then
        -- 갱신 결제 실패: 이용권은 이미 받은 기간까지 유지하고 구독을 past_due로 바꾼다.
        update public.billing_subscriptions set status = 'past_due', updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id and status = 'active';
        return case when found then 'applied' else 'ignored' end;

    elsif p_kind = 'subscription_cancelled' then
        -- 해지: 이미 결제한 기간이 끝날 때까지 이용 가능하고 다음 갱신은 하지 않는다.
        update public.billing_subscriptions set cancel_at_period_end = true, updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id and status in ('active', 'past_due');
        return case when found then 'applied' else 'ignored' end;

    elsif p_kind = 'subscription_expired' then
        update public.billing_subscriptions set status = 'expired', updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id;
        update public.billing_entitlements set valid_until = least(valid_until, now()), updated_at = now()
        where user_id = v_order.user_id and plan_id = v_order.plan_id;
        return 'applied';
    end if;

    raise exception 'billing_apply_event: unknown kind %', p_kind;
end;
$$;


-- 이용권 확인. 앱이 기능 제한에 쓴다.
create or replace function public.billing_has_entitlement(p_plan_id text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.billing_entitlements
        where user_id = auth.uid() and plan_id = p_plan_id and valid_until > now()
    );
$$;


-- 회원탈퇴 전에 결제 기록을 별도 보존 영역으로 옮긴다(service_role 전용).
create or replace function public.billing_retain_orders_for_user(p_user_id uuid)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_count integer;
begin
    insert into billing_private.retained_orders (
        order_id, plan_id, amount, currency, status, provider, provider_payment_id,
        created_at, paid_at, retain_until
    )
    select order_id, plan_id, amount, currency, status, provider, provider_payment_id,
           created_at, paid_at, coalesce(paid_at, created_at) + interval '5 years'
    from public.billing_orders
    where user_id = p_user_id and status in ('paid', 'refunded')
    on conflict (order_id) do nothing;
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

revoke execute on function public.billing_create_order(text, text) from public, anon;
revoke execute on function public.billing_apply_event(text, text, text, text, uuid, text, integer, text) from public, anon, authenticated;
revoke execute on function public.billing_has_entitlement(text) from public, anon;
revoke execute on function public.billing_retain_orders_for_user(uuid) from public, anon, authenticated;
grant execute on function public.billing_create_order(text, text) to authenticated;
grant execute on function public.billing_has_entitlement(text) to authenticated;
grant execute on function public.billing_apply_event(text, text, text, text, uuid, text, integer, text) to service_role;
grant execute on function public.billing_retain_orders_for_user(uuid) to service_role;
