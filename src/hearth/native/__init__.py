"""The doorway the native app asks hearth through.

The Mac app starts ``hearth bridge`` as a process of its own and talks to it over that process's
standard input and output, one JSON object a line (``hearth.native.stdio``). There is no socket
and no port: the only thing that can ask is the app that started the process, and it ends when
the app closes its end.

The bridge answers nothing about the household until the app signs in as a member with their
password, which hearth checks as the pages' sign-in does (``hearth.native.bridge``). From then
on every answer is a view from ``hearth.disclosure``, for that one member, written out as plain
data (``hearth.native.doorway``). The doorway decides nothing about who may see what, reads no
ledger, and is held to the rules the pages are held to (``tests/test_architecture.py``).
Anything the viewer may not see answers exactly like something that is not there.
"""
