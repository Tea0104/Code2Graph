/**
 * @name Inheritance Edges (Using Class)
 * @description Extracts class inheritance relationships using Class type.
 * @kind table
 * @id python/inherits-v2
 */

import python

from Class child, Class parent
where parent = child.getASuperType()
select
  "INHERITS" as rel,
  child.getLocation().getFile().getRelativePath() as fromFile,
  child.getName() as fromName,
  child.getLocation().getStartLine() as fromLine,
  parent.getLocation().getFile().getRelativePath() as toFile,
  parent.getName() as toName,
  parent.getLocation().getStartLine() as toLine
