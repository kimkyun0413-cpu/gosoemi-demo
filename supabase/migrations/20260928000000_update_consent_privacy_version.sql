-- Keep the consent RPC's accepted privacy-policy version aligned with app.py.
-- This migration deliberately replaces only the previous literal inside the
-- existing function so its security settings and insert logic remain unchanged.
do $$
declare
    v_function_definition text;
begin
    select pg_get_functiondef(
        'public.record_consent(text,text)'::regprocedure
    )
    into v_function_definition;

    if position('''2026-08-30''' in v_function_definition) = 0 then
        raise exception
            'record_consent migration stopped: expected privacy version was not found';
    end if;

    v_function_definition := replace(
        v_function_definition,
        '''2026-08-30''',
        '''2026-09-27'''
    );

    execute v_function_definition;
end;
$$;
