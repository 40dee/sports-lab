import ast,unittest
from pathlib import Path
TREE=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text())
class HtmlRenderingTests(unittest.TestCase):
 def test_optional_empty_fragments_do_not_create_markdown_code_blocks(self):
  env={};node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='ui_html')
  exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  output=env['ui_html']('<div>\n      <span>Pick</span>\n      \n      <div class="sl-footer">Review</div>\n    </div>')
  self.assertEqual(output,'<div>\n<span>Pick</span>\n<div class="sl-footer">Review</div>\n</div>')
 def test_all_requested_views_normalize_unsafe_html_arguments(self):
  for node in TREE.body:
   if isinstance(node,ast.FunctionDef) and node.name in ('ui_card','ui_detail','render_combos','render_results_dashboard'):
    for call in ast.walk(node):
     if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='markdown' and any(k.arg=='unsafe_allow_html' and isinstance(k.value,ast.Constant) and k.value.value is True for k in call.keywords):
      self.assertIsInstance(call.args[0],ast.Call,node.name)
      self.assertEqual(call.args[0].func.id,'ui_html',node.name)
 def test_results_four_view_mobile_navigation(self):
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='render_results_dashboard')
  controls=[call for call in ast.walk(node) if isinstance(call,ast.Call)
            and isinstance(call.func,ast.Attribute) and call.func.attr=='segmented_control']
  results=[call for call in controls if call.args and isinstance(call.args[0],ast.Constant)
           and call.args[0].value=='Results view']
  self.assertEqual(len(results),1)
  self.assertEqual([item.value for item in results[0].args[1].elts],
                   ['Overview','Sharp +EV','Pick Log','Audits'])
  self.assertTrue(any(k.arg=='key' and k.value.value=='results_view_tabs'
                      for k in results[0].keywords))
 def test_results_pick_log_uses_safe_html_and_pagination(self):
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='render_results_dashboard')
  source=ast.unparse(node)
  self.assertIn('result_table_html(page_rows)',source)
  self.assertIn('math.ceil(len(visible) / 25)',source)
  self.assertIn('ui_text(label)',source)
  self.assertIn('ui_text(value)',source)
if __name__=='__main__':unittest.main()
