import ast
import contextlib
import io
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from google.genai import types
from ai_runtime import SupabaseUsageStore, UsageStoreUnavailable, generation_settings, public_ai_blockers, usage_counts
import ai_runtime as runtime

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ControlsTest(unittest.TestCase):
    def test_export_includes_only_own_usage_without_credentials(self):
        import datetime, json, zipfile
        from data_export import build_text_part
        account={'email':'fake@example.invalid','method':'fake','ai_usage':[
            {'user_id':'own','request_id':'r','total_tokens':12,'api_key':'SECRET'},
            {'user_id':'other','request_id':'foreign','total_tokens':99}]}
        result=build_text_part('own',account,[],{},{},[],[],[],datetime.datetime(2026,10,14))
        with zipfile.ZipFile(io.BytesIO(result['text_part'])) as z:
            path=next(p for p in z.namelist() if p.endswith('/AI사용량기록.json'))
            text=z.read(path).decode()
        self.assertNotIn('SECRET',text)
        self.assertNotIn('foreign',text)
        self.assertEqual(json.loads(text)[0]['total_tokens'],12)

    def test_each_feature_has_sdk_compatible_limits(self):
        for feature in ('chat','voice_chat','draft','rewrite','transcribe','extract','summary'):
            cfg = types.GenerateContentConfig(**generation_settings(feature))
            self.assertGreater(cfg.max_output_tokens, 0)
            self.assertLessEqual(cfg.max_output_tokens, 8192)
            self.assertIsNotNone(cfg.thinking_config.thinking_level)

    def test_missing_usage_is_unknown_and_thinking_not_added_to_total(self):
        self.assertIsNone(usage_counts(None)['total_token_count'])
        counts = usage_counts({'prompt_token_count': 100, 'candidates_token_count': 50,
                               'thoughts_token_count': 25, 'total_token_count': 175})
        self.assertEqual(counts['total_token_count'], 175)
        self.assertIsNone(usage_counts({'total_token_count': -1})['total_token_count'])

    def test_public_launch_requires_every_condition(self):
        ready = dict(paid=True, hosting=True, age=18, privacy_v3=True, durable=True)
        self.assertEqual(public_ai_blockers(**ready), [])
        for key in ready:
            bad = {**ready, key: 14 if key == 'age' else False}
            self.assertEqual(len(public_ai_blockers(**bad)), 1)

    def test_rpc_only_contains_usage_metadata(self):
        client = Mock()
        client.rpc.return_value.execute.return_value.data = 'reserved'
        store = SupabaseUsageStore(client)
        self.assertEqual(store.reserve('user','request','draft','model',
                         dict(user_hour=30,total_hour=300,user_day=100,total_day=2000)), 'reserved')
        client.rpc.return_value.execute.return_value.data = True
        store.finish('request', 'succeeded', SimpleNamespace(prompt_token_count=12, total_token_count=30,
                      prompt='PRIVATE', response='PRIVATE', api_key='SECRET'))
        payload = client.rpc.call_args.args[1]
        self.assertNotIn('PRIVATE', str(payload))
        self.assertNotIn('SECRET', str(payload))
        self.assertEqual(payload['p_input'], 12)

    def test_bad_rpc_results_fail_closed(self):
        client = Mock()
        client.rpc.return_value.execute.return_value.data = None
        store = SupabaseUsageStore(client)
        with self.assertRaises(runtime.UsageStoreUnavailable):
            store.reserve('u','r','chat','m',dict(user_hour=1,total_hour=1,user_day=1,total_day=1))


def app_functions():
    tree = ast.parse((ROOT / 'app.py').read_text())
    names = {'call_gemini','finish_ai_request','reserve_ai_call','request_tts_audio','ai_unavailable_for_current_user'}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    source = ast.Module(body=functions, type_ignores=[])
    ns = dict(types=types, generation_settings=generation_settings, usage_counts=usage_counts,
              public_ai_blockers=public_ai_blockers)
    import json, uuid
    ns.update(json=json, uuid=uuid)
    class Limit(RuntimeError): pass
    ns.update(AiUsageLimitError=Limit, AiPausedError=Limit)
    exec(compile(source, str(ROOT/'app.py'), 'exec'), ns)
    return ns


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.ns = app_functions()
        self.store = Mock()
        self.store.reserve.return_value = 'reserved'
        self.ns.update(AI_FEATURES_PAUSED=False, AI_PUBLIC_LAUNCH_ENABLED=False, AI_USAGE_DB_ENABLED=True,
            AI_CALLS_PER_HOUR=30, AI_CALLS_PER_HOUR_TOTAL=300, AI_CALLS_PER_DAY=100, AI_CALLS_PER_DAY_TOTAL=2000,
            GEMINI_MODEL='gemini-3.5-flash-lite', GEMINI_TIMEOUT_MS=90000,
            AI_PAUSED_MESSAGE='paused', st=SimpleNamespace(session_state={'supabase_session': {'user_id':'u'}}),
            is_admin=lambda:True,
            verified_user_id=lambda s:'u', get_ai_usage_store=lambda:self.store,
            os=SimpleNamespace(environ={'GEMINI_API_KEY':'NEVER_LOG'}))
        self.models = Mock()
        self.models.generate_content.return_value = SimpleNamespace(text='answer',
            usage_metadata=SimpleNamespace(prompt_token_count=100,candidates_token_count=50,total_token_count=150))
        self.ns['genai'] = SimpleNamespace(Client=lambda **k: SimpleNamespace(models=self.models))

    def test_request_records_real_usage_and_explicit_draft_limit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.ns['call_gemini']('PRIVATE', feature='draft'), 'answer')
        self.assertNotIn('PRIVATE', output.getvalue())
        self.assertNotIn('NEVER_LOG', output.getvalue())
        cfg = self.models.generate_content.call_args.kwargs['config']
        self.assertEqual(cfg.max_output_tokens, 8192)
        self.assertEqual(self.store.reserve.call_args.args[2], 'draft')
        self.assertEqual(self.store.finish.call_args.args[1], 'succeeded')

    def test_reservation_error_makes_zero_ai_calls(self):
        self.store.reserve.side_effect = RuntimeError('secret-sensitive-error')
        with self.assertRaises(self.ns['AiPausedError']) as e:
            self.ns['call_gemini']('PRIVATE')
        self.assertNotIn('secret', str(e.exception))
        self.models.generate_content.assert_not_called()

    def test_daily_limit_makes_zero_ai_calls(self):
        self.store.reserve.return_value = 'user_day'
        with self.assertRaises(self.ns['AiUsageLimitError']): self.ns['call_gemini']('PRIVATE')
        self.models.generate_content.assert_not_called()

    def test_failed_api_stays_counted_and_records_failure(self):
        self.models.generate_content.side_effect = RuntimeError('api unavailable')
        with self.assertRaises(RuntimeError): self.ns['call_gemini']('PRIVATE')
        self.assertEqual(self.store.finish.call_args.args[1], 'failed')

    def test_cut_off_response_is_not_returned_as_completed_draft(self):
        self.models.generate_content.return_value.candidates = [SimpleNamespace(finish_reason='MAX_TOKENS')]
        with self.assertRaisesRegex(RuntimeError, 'output limit'): self.ns['call_gemini']('PRIVATE',feature='draft')

    def test_usage_completion_failure_keeps_answer_and_no_secret_log(self):
        self.store.finish.side_effect = RuntimeError('PRIVATE KEY')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.ns['call_gemini']('PRIVATE'), 'answer')
        self.assertIn('completion_unavailable', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())

    def test_public_launch_with_incomplete_config_makes_zero_ai_calls(self):
        self.ns.update(AI_PUBLIC_LAUNCH_ENABLED=True, MIN_USER_AGE=14, privacy_v3_in_effect=lambda:False)
        with self.assertRaises(self.ns['AiPausedError']): self.ns['call_gemini']('PRIVATE')
        self.models.generate_content.assert_not_called()
        self.store.reserve.assert_not_called()

    def test_pause_overrides_all_other_settings(self):
        self.ns['AI_FEATURES_PAUSED'] = True
        with self.assertRaises(self.ns['AiPausedError']): self.ns['call_gemini']('PRIVATE')
        self.models.generate_content.assert_not_called()

    def test_regular_user_cannot_bypass_launch_by_turning_pause_off(self):
        self.ns['is_admin'] = lambda:False
        with self.assertRaises(self.ns['AiPausedError']): self.ns['call_gemini']('PRIVATE')
        self.models.generate_content.assert_not_called()


class DraftRpcTest(unittest.TestCase):
    def setUp(self):
        tree=ast.parse((ROOT/'app.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='save_case_draft')
        self.client=Mock()
        self.ns=dict(DRAFT_ATOMIC_SAVE_ENABLED=True, CASE_DRAFT_MAX_CHARS=20000,
            ensure_active_session=lambda:{'user_id':'u'}, get_authed_client=lambda:self.client,
            SaveConflict=type('SaveConflict',(RuntimeError,),{}))
        exec(compile(ast.Module(body=[function],type_ignores=[]),'app.py','exec'),self.ns)

    def test_checked_draft_save_uses_one_rpc_with_original_timestamp(self):
        self.client.rpc.return_value.execute.return_value.data=[{'updated_at':'new'}]
        self.assertEqual(self.ns['save_case_draft']('case','text',expected_at='old'),'new')
        self.client.rpc.assert_called_once_with('save_case_draft_checked',
            dict(p_case_id='case',p_text='text',p_expected='old',p_check=True))
        self.client.table.assert_not_called()

    def test_empty_rpc_result_is_conflict_not_success(self):
        self.client.rpc.return_value.execute.return_value.data=[]
        with self.assertRaises(self.ns['SaveConflict']): self.ns['save_case_draft']('case','text',expected_at=None)

    def test_explicit_overwrite_is_distinct_from_first_save(self):
        self.client.rpc.return_value.execute.return_value.data=[{'updated_at':'new'}]
        self.ns['save_case_draft']('case','text',expected_at=False)
        self.assertFalse(self.client.rpc.call_args.args[1]['p_check'])


if __name__ == '__main__': unittest.main()
