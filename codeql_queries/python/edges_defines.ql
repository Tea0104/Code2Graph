import python

// 查询定义关系，连接模块、类和函数到它们定义的成员。

predicate definesEdge(
  string rel,
  string fromFile,
  string fromName,
  int fromLine,
  string toFile,
  string toName,
  int toLine
) {
  exists(Module m, Class c |
    c.getEnclosingScope() = m and
    rel = "DEFINES" and
    fromFile = m.getFile().getRelativePath() and
    fromName = m.getFile().getBaseName() and
    fromLine = 1 and
    toFile = c.getLocation().getFile().getRelativePath() and
    toName = c.getName() and
    toLine = c.getLocation().getStartLine()
  )
  or
  exists(Module m, Function f |
    f.getEnclosingScope() = m and
    not f.isMethod() and
    rel = "DEFINES" and
    fromFile = m.getFile().getRelativePath() and
    fromName = m.getFile().getBaseName() and
    fromLine = 1 and
    toFile = f.getLocation().getFile().getRelativePath() and
    toName = f.getName() and
    toLine = f.getLocation().getStartLine()
  )
  or
  exists(Module m, AssignStmt a, GlobalVariable v |
    a.defines(v) and
    a.getScope() = m and
    rel = "DEFINES" and
    fromFile = m.getFile().getRelativePath() and
    fromName = m.getFile().getBaseName() and
    fromLine = 1 and
    toFile = a.getLocation().getFile().getRelativePath() and
    toName = v.getId() and
    toLine = a.getLocation().getStartLine()
  )
  or
  exists(Class c, Function f |
    f.getScope() = c and
    f.isMethod() and
    rel = "DEFINES" and
    fromFile = c.getLocation().getFile().getRelativePath() and
    fromName = c.getName() and
    fromLine = c.getLocation().getStartLine() and
    toFile = f.getLocation().getFile().getRelativePath() and
    toName = f.getName() and
    toLine = f.getLocation().getStartLine()
  )
  or
  exists(Class c, Attribute a |
    a.getScope() = c and
    rel = "DEFINES" and
    fromFile = c.getLocation().getFile().getRelativePath() and
    fromName = c.getName() and
    fromLine = c.getLocation().getStartLine() and
    toFile = a.getLocation().getFile().getRelativePath() and
    toName = a.getName() and
    toLine = a.getLocation().getStartLine()
  )
  or
  exists(Function f, LocalVariable v |
    v.getScope() = f and
    rel = "DEFINES" and
    fromFile = f.getLocation().getFile().getRelativePath() and
    fromName = f.getName() and
    fromLine = f.getLocation().getStartLine() and
    toFile = v.getAnAccess().getLocation().getFile().getRelativePath() and
    toName = v.getId() and
    toLine = v.getAnAccess().getLocation().getStartLine()
  )
}

from string rel, string fromFile, string fromName, int fromLine, string toFile, string toName, int toLine
where definesEdge(rel, fromFile, fromName, fromLine, toFile, toName, toLine)
select rel, fromFile, fromName, fromLine, toFile, toName, toLine