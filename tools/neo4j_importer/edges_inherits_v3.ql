/**
 * @name Inheritance Edges (Corrected)
 * @description Extracts class inheritance using getABaseClass.
 * @kind table
 * @id python/inherits-corrected
 */

import python

from Class child, Class parent
where parent = child.getABaseClass()
select
  "INHERITS" as rel,
  child.getLocation().getFile().getRelativePath() as fromFile,
  child.getName() as fromName,
  child.getLocation().getStartLine() as fromLine,
  parent.getLocation().getFile().getRelativePath() as toFile,
  parent.getName() as toName,
  parent.getLocation().getStartLine() as toLine
