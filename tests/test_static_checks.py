"""Statische Pruefungen ohne Ausfuehrung von Hardwarecode.

* Syntax aller Projektdateien (inkl. Legacy, Startskripte, Tests)
* keine zirkulaeren Importe zwischen Projektmodulen
* jede ``from <projektmodul> import name``-Anweisung verweist auf einen existierenden Namen
  (erkennt Referenzen auf entfernte Klassen)
* ``Driving.<Name>``/``driving.<Name>`` existiert in ``driving.__all__`` (Startskripte)
* Dateien aus DELETE_MANIFEST.txt werden von keinem verbleibenden Modul importiert
"""
import ast
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = ('camp2code/', '.git/', 'legacy/removed/')


def manifest_entries():
    path = ROOT / 'DELETE_MANIFEST.txt'
    if not path.is_file():
        return set()
    return {line.split('#')[0].strip() for line in path.read_text(encoding='utf-8').splitlines()
            if line.split('#')[0].strip()}


def project_files(include_deleted=False):
    deleted = manifest_entries()
    for path in sorted(ROOT.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(EXCLUDE_DIRS):
            continue
        if not include_deleted and rel in deleted:
            continue
        yield rel, path


def module_name(rel):
    name = rel[:-3].replace('/', '.')
    return name[:-9] if name.endswith('.__init__') else name


def top_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for item in ast.walk(target):
                    if isinstance(item, ast.Name):
                        names.add(item.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.Try):
            for sub in node.body + [h for handler in node.handlers for h in handler.body]:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add((alias.asname or alias.name).split('.')[0])
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
    return names


class StaticChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trees, cls.modules = {}, {}
        for rel, path in project_files(include_deleted=True):
            cls.trees[rel] = ast.parse(path.read_text(encoding='utf-8'), filename=rel)
        for rel, tree in cls.trees.items():
            cls.modules[module_name(rel)] = (rel, tree)

    def resolve(self, current, node):
        if not node.level:
            return node.module
        package = module_name(current)
        if not current.endswith('__init__.py'):
            package = package.rpartition('.')[0]
        parts = package.split('.') if package else []
        if node.level > 1:
            parts = parts[:len(parts) - (node.level - 1)]
        return '.'.join(parts + ([node.module] if node.module else []))

    def exported(self, name):
        if name not in self.modules:
            return set()
        tree = self.modules[name][1]
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == '__all__' for t in node.targets):
                return {e.value for e in node.value.elts}
        return {n for n in top_level_names(tree) if not n.startswith('_')}

    def test_syntax(self):
        for rel, path in project_files(include_deleted=True):
            compile(path.read_text(encoding='utf-8'), rel, 'exec')

    def test_keine_zirkulaeren_importe(self):
        graph = defaultdict(set)
        for rel, tree in self.trees.items():
            if rel.startswith('tests/'):
                continue
            name = module_name(rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in self.modules:
                            graph[name].add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    base = self.resolve(rel, node)
                    for alias in node.names:
                        full = f'{base}.{alias.name}'
                        if full in self.modules:
                            graph[name].add(full)
                        elif base in self.modules and base != name:
                            graph[name].add(base)
        # Paket-__init__ importiert Untermodule; Untermodule duerfen nicht zurueck auf das Paket zeigen.
        visiting, done, cycles = set(), set(), []

        def visit(node, path):
            if node in visiting:
                cycles.append(path[path.index(node):] + [node])
                return
            if node in done:
                return
            visiting.add(node)
            for nxt in graph.get(node, ()):
                if not (nxt == node.rpartition('.')[0]):
                    visit(nxt, path + [nxt])
            visiting.discard(node)
            done.add(node)

        for start in list(graph):
            visit(start, [start])
        self.assertEqual(cycles, [])

    def test_importierte_namen_existieren(self):
        deleted = manifest_entries()
        problems = []
        for rel, tree in self.trees.items():
            if rel in deleted:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    base = self.resolve(rel, node)
                    if base not in self.modules:
                        continue
                    target_rel, target_tree = self.modules[base]
                    available = top_level_names(target_tree)
                    for sub in target_tree.body:  # Sternimporte (z. B. Driving.py) aufloesen
                        if isinstance(sub, ast.ImportFrom) and any(a.name == '*' for a in sub.names):
                            available |= self.exported(self.resolve(target_rel, sub))
                    for alias in node.names:
                        if alias.name == '*' or f'{base}.{alias.name}' in self.modules:
                            continue
                        if alias.name not in available:
                            problems.append(f'{rel}: {alias.name} fehlt in {target_rel}')
        self.assertEqual(problems, [])

    def test_fahrmodus_referenzen(self):
        init = self.modules['driving'][1]
        exported = set()
        for node in init.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == '__all__' for t in node.targets):
                exported = {e.value for e in node.value.elts}
        deleted = manifest_entries()
        problems = []
        for rel, tree in self.trees.items():
            if rel in deleted:
                continue
            aliases = {(a.asname or a.name) for n in ast.walk(tree) if isinstance(n, ast.Import)
                       for a in n.names if a.name in ('Driving', 'driving')}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id in aliases and node.attr not in exported):
                    problems.append(f'{rel}:{node.lineno} Driving.{node.attr}')
        self.assertEqual(problems, [])

    def test_geloeschte_dateien_unbenutzt(self):
        deleted = {module_name(entry) for entry in manifest_entries() if entry.endswith('.py')}
        users = []
        for rel, tree in self.trees.items():
            if rel in manifest_entries():
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [self.resolve(rel, node)]
                users += [f'{rel} -> {n}' for n in names if n in deleted]
        self.assertEqual(users, [])

    def test_manifest_nur_vorhandene_oder_bereits_entfernte_eintraege(self):
        for entry in manifest_entries():
            self.assertFalse(entry.startswith(('/', '..')), entry)
            self.assertNotIn(entry, ('BaseCar.py', 'basisklassen.py', 'basisklassen_cam.py',
                                     'Driving.py', 'config.json', 'pipeline_studio.json'))


if __name__ == '__main__':
    unittest.main()
