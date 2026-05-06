import python
import semmle.python.types.ClassObject

// 查询 Python 类的继承关系，输出子类到父类的继承边。

from Class c, Class base
where
  exists(ClassObject cObj, ClassObject baseObj |
    cObj.getPyClass().getScope() = c and
    baseObj.getPyClass().getScope() = base and
    baseObj = cObj.getABaseType()
  )
select
  "INHERITS" as rel,
  c.getLocation().getFile().getRelativePath() as fromFile,
  c.getName() as fromName,
  c.getLocation().getStartLine() as fromLine,
  base.getLocation().getFile().getRelativePath() as toFile,
  base.getName() as toName,
  base.getLocation().getStartLine() as toLine
