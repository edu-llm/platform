"""What the platform says when something happens, and how it says it.

Three modules and one seam. ``facts`` reads an event, ``messages`` words it, ``delivery``
sends it. Nothing is re-exported here, so importing the package costs nothing and a module
that wants the reader does not drag the transport in behind it.
"""
