import sys,tempfile,unittest,json,os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def native_result(args,init_code=0,**kwargs):
 if args[1:3]==['config','show']:
  exists=(Path(os.environ['ZINIAO_CLI_CONFIG_DIR'])/'config.json').exists()
  return SimpleNamespace(returncode=1,stdout='',stderr='配置文件格式无效' if exists else '未找到配置文件')
 return SimpleNamespace(returncode=init_code,stdout='',stderr='')
class PreflightTests(unittest.TestCase):
 def test_classifies_login_permissions_and_network_without_retrying_download(self):
  from ziniao_preflight import page_error
  self.assertEqual(page_error({'url':'https://wing.coupang.com/login','text':'','password':True}),'LOGIN_REQUIRED')
  self.assertEqual(page_error({'url':'https://wing.coupang.com/x','text':'Access Denied'}),'PERMISSION_DENIED')
  self.assertEqual(page_error({'url':'chrome-error://chromewebdata/','text':'ERR_SOCKS_CONNECTION_FAILED'}),'NETWORK_UNAVAILABLE')
  self.assertEqual(page_error({'url':'https://wing.coupang.com/x','captcha':True}),'CAPTCHA_REQUIRED')
 def test_explicit_store_cannot_override_bound_directory(self):
  from ziniao_environment import select_store
  with self.assertRaises(ValueError):select_store({'browser_id':'1'},'2')
 def test_secret_environment_is_isolated_and_restored(self):
  from ziniao_environment import environment,redact
  with patch.dict(os.environ,{'TEST_WING_KEY':'dummy-test-secret'},clear=False),patch('ziniao_environment.subprocess.run',side_effect=native_result) as call:
   args=SimpleNamespace(ziniao_api_key_env='TEST_WING_KEY',ziniao_api_key_stdin=False,ziniao_config_dir=None)
   previous=os.environ.get('ZINIAO_CLI_CONFIG_DIR')
   with environment(args):
    path=Path(os.environ['ZINIAO_CLI_CONFIG_DIR']);self.assertTrue(path.exists());self.assertNotIn('dummy-test-secret',str(call.call_args.args));self.assertEqual(redact('error dummy-test-secret'),'error [REDACTED]')
   self.assertFalse(path.exists());self.assertEqual(os.environ.get('ZINIAO_CLI_CONFIG_DIR'),previous)
 def test_failed_doctor_stops_before_store_or_page_access(self):
  from ziniao_preflight import check
  with tempfile.TemporaryDirectory() as t,patch('ziniao_preflight.invoke',return_value='API Key 无效') as invoke:
   with self.assertRaises(RuntimeError):check(Path(t),{'browser_id':'1','store_id':'ziniao:1','store_name':'店','expected_title':'店'})
   self.assertEqual(invoke.call_count,1)
 def test_store_mismatch_never_navigates(self):
  from ziniao_preflight import check
  with tempfile.TemporaryDirectory() as t,patch('ziniao_preflight.doctor',return_value={}),patch('ziniao_preflight.store_items',return_value=[{'browser_id':'2','store_name':'别店'}]),patch('ziniao_preflight.invoke') as invoke:
   with self.assertRaises(RuntimeError):check(Path(t),{'browser_id':'1','store_id':'ziniao:1','store_name':'店','expected_title':'店'})
   invoke.assert_not_called()
 def test_failed_dynamic_key_does_not_change_default_configuration(self):
  from ziniao_environment import environment,redact
  with patch.dict(os.environ,{'TEST_WING_KEY':'dummy-secret'},clear=False),patch('ziniao_environment.subprocess.run',side_effect=lambda args,**kw:native_result(args,init_code=1,**kw)):
   previous=os.environ.get('ZINIAO_CLI_CONFIG_DIR')
   with self.assertRaisesRegex(RuntimeError,'API_KEY_INIT_FAILED'):
    with environment(SimpleNamespace(ziniao_api_key_env='TEST_WING_KEY',ziniao_api_key_stdin=False,ziniao_config_dir=None)):self.fail('must not run')
   self.assertEqual(os.environ.get('ZINIAO_CLI_CONFIG_DIR'),previous)
 def test_socks_retry_is_bounded_and_navigation_only(self):
  from ziniao_preflight import invoke
  bad=SimpleNamespace(returncode=1,stdout='',stderr='ERR_SOCKS_CONNECTION_FAILED')
  with patch('ziniao_preflight.subprocess.run',return_value=bad) as run,patch('ziniao_preflight.time.sleep'):
   with self.assertRaises(RuntimeError):invoke(['page','visit','--url','https://wing.coupang.com/'])
   self.assertEqual(run.call_count,3)
  with patch('ziniao_preflight.subprocess.run',return_value=bad) as run,patch('ziniao_preflight.time.sleep'):
   with self.assertRaises(RuntimeError):invoke(['page','click','--selector','#download'])
   self.assertEqual(run.call_count,1)

 def test_unsupported_isolation_never_receives_secret(self):
  from ziniao_environment import environment
  with patch.dict(os.environ,{'TEST_WING_KEY':'dummy-secret'},clear=False),patch('ziniao_environment.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='existing profile',stderr='')) as run:
   with self.assertRaisesRegex(RuntimeError,'CONFIG_ISOLATION_UNVERIFIED'):
    with environment(SimpleNamespace(ziniao_api_key_env='TEST_WING_KEY',ziniao_api_key_stdin=False,ziniao_config_dir=None)):self.fail('must stop')
   self.assertTrue(all('init' not in c.args[0] for c in run.call_args_list))
 def test_doctor_does_not_ignore_account_mismatch_after_positive_checks(self):
  from ziniao_preflight import doctor
  text='API Key 有效\n客户端登录用户: boss\nBridge 连通正常\n✗ 当前客户端账号不匹配\n检查 Skills...'
  with patch('ziniao_preflight.invoke',return_value=text):
   with self.assertRaises(RuntimeError):doctor()
 def test_wing_auth_redirect_is_login_required_without_oauth_query_in_error(self):
  from ziniao_preflight import invoke
  bad=SimpleNamespace(returncode=1,stdout='',stderr='expected wing.coupang.com, got xauth.coupang.com https://xauth.coupang.com/auth?state=opaque-test-value')
  with patch('ziniao_preflight.subprocess.run',return_value=bad):
   with self.assertRaisesRegex(RuntimeError,'LOGIN_REQUIRED') as result:invoke(['page','visit','--url','https://wing.coupang.com/'])
   self.assertNotIn('opaque-test-value',str(result.exception))

class NonCredentialPageCheckTests(unittest.TestCase):
 def test_page_probe_does_not_query_password_controls(self):
  from ziniao_preflight import PAGE
  self.assertNotIn('input[type=password]',PAGE)
  self.assertNotIn("querySelectorAll('input",PAGE)

class ReuseCurrentPageTests(unittest.TestCase):
 def check_with_page(self,info):
  from ziniao_preflight import check
  from contextlib import ExitStack
  with tempfile.TemporaryDirectory() as t,ExitStack() as stack:
   stack.enter_context(patch('ziniao_preflight.doctor',return_value={}))
   stack.enter_context(patch('ziniao_preflight.store_items',return_value=[{'browser_id':'1','store_name':'店'}]))
   invoke=stack.enter_context(patch('ziniao_preflight.invoke',return_value=json.dumps({'ok':True,'data':{'result':json.dumps(info)}})))
   result=check(Path(t),{'browser_id':'1','store_id':'ziniao:1','store_name':'店','expected_title':'公司A','ads_identity':'A00000000'})
   return result,[c.args[0] for c in invoke.call_args_list]
 def test_wing_page_is_reused_without_home_navigation(self):
  report,calls=self.check_with_page({'url':'https://wing.coupang.com/tenants/business-insight/sales-analysis','title':'Coupang Wing 公司A','text':''})
  self.assertTrue(report['wing_identity_verified']);self.assertFalse(any(c[:2]==['page','visit'] for c in calls))
 def test_verified_ads_page_does_not_switch_back_to_wing(self):
  report,calls=self.check_with_page({'url':'https://advertising.coupang.com/marketing-reporting/','title':'Ads','text':'广告账户 A00000000'})
  self.assertTrue(report['identity_verified']);self.assertFalse(any(c[:2]==['page','visit'] for c in calls))

class CurrentPageBoundaryTests(unittest.TestCase):
 def test_wrong_ads_merchant_stops_without_home_navigation(self):
  import ziniao_preflight as z
  info={'url':'https://advertising.coupang.com/reports','title':'Ads','text':'广告账户 A99999999'}
  with tempfile.TemporaryDirectory() as t,patch.object(z,'doctor',return_value={}),patch.object(z,'store_items',return_value=[{'browser_id':'1','store_name':'店'}]),patch.object(z,'invoke',return_value=json.dumps({'ok':True,'data':{'result':json.dumps(info)}})) as invoke,patch.object(z.time,'monotonic',side_effect=[0,21]):
   with self.assertRaisesRegex(RuntimeError,'STORE_PAGE_UNVERIFIED'):z.check(Path(t),{'store_id':'ziniao:1','browser_id':'1','store_name':'店','expected_title':'公司A','ads_identity':'A00000000'})
   self.assertFalse(any(c.args[0][:2]==['page','visit'] for c in invoke.call_args_list))
 def test_unrelated_page_uses_at_most_one_wing_navigation(self):
  import ziniao_preflight as z
  pages=iter([{'url':'https://www.coupang.com/vp/products/1','title':'商品','text':''},{'url':'https://wing.coupang.com/','title':'Wing 公司A','text':''}])
  def call(args):
   if args[:2]==['page','visit']:return ''
   return json.dumps({'ok':True,'data':{'result':json.dumps(next(pages))}})
  with tempfile.TemporaryDirectory() as t,patch.object(z,'doctor',return_value={}),patch.object(z,'store_items',return_value=[{'browser_id':'1','store_name':'店'}]),patch.object(z,'invoke',side_effect=call) as invoke:
   r=z.check(Path(t),{'store_id':'ziniao:1','browser_id':'1','store_name':'店','expected_title':'公司A'})
   self.assertEqual(r['page_verification'],'navigated_to_wing_once');self.assertEqual(sum(c.args[0][:2]==['page','visit'] for c in invoke.call_args_list),1)
