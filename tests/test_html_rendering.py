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
if __name__=='__main__':unittest.main()
