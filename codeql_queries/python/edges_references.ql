import python
import semmle.python.SelfAttribute

// 查询函数/方法对手写全局变量和手写类属性的引用关系。

predicate handWrittenClassAttribute(Class c, string name, string file, int line) {
  exists(Attribute a |
    a.getScope() = c and
    a.getName() = name and
    file = a.getLocation().getFile().getRelativePath() and
    line = a.getLocation().getStartLine()
  )
  or
  exists(AnnAssign a, Name target |
    a.getScope() = c and
    target = a.getTarget() and
    target.getId() = name and
    file = a.getLocation().getFile().getRelativePath() and
    line = a.getLocation().getStartLine()
  )
}

predicate handWrittenGlobalVariable(string name, string file, int line) {
  exists(Module m, AssignStmt a, GlobalVariable v |
    a.defines(v) and
    a.getScope() = m and
    v.getId() = name and
    file = a.getLocation().getFile().getRelativePath() and
    line = a.getLocation().getStartLine()
  )
}

predicate referencesEdge(
  string rel,
  string fromFile,
  string fromName,
  int fromLine,
  string toFile,
  string toName,
  int toLine
) {
  exists(Function f, Name n, GlobalVariable v, string defFile, int defLine |
    n.getScope() = f and
    handWrittenGlobalVariable(v.getId(), defFile, defLine) and
    n.uses(v) and
    rel = "REFERENCES" and
    fromFile = f.getLocation().getFile().getRelativePath() and
    fromName = f.getName() and
    fromLine = f.getLocation().getStartLine() and
    toFile = defFile and
    toName = v.getId() and
    toLine = defLine
  )
  or
  exists(Function f, SelfAttributeRead sa, Class c, string defFile, int defLine |
    sa.getScope() = f and
    sa.getClass() = c and
    handWrittenClassAttribute(c, sa.getName(), defFile, defLine) and
    rel = "REFERENCES" and
    fromFile = f.getLocation().getFile().getRelativePath() and
    fromName = f.getName() and
    fromLine = f.getLocation().getStartLine() and
    toFile = defFile and
    toName = sa.getName() and
    toLine = defLine
  )
}

from string rel, string fromFile, string fromName, int fromLine, string toFile, string toName, int toLine
where referencesEdge(rel, fromFile, fromName, fromLine, toFile, toName, toLine)
select rel, fromFile, fromName, fromLine, toFile, toName, toLine
