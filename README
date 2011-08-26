Read me first
=============

This package contains the C bindings to the CIEL executor
interface, `libciel.so`. To build this library, you must first install
the [Jansson JSON library](http://www.digip.org/jansson/). Thereafter,
the library can be built by typing `make` in the root directory.

Note that, by convention, all programs using the CIEL executor
interface are invoked as follows:

`EXECUTABLE_NAME --write-fifo WRITE_FIFO_NAME --read-fifo READ_FIFO_NAME`

The C bindings are initialised using the `ciel_init()`
function. Therefore, your program should include the following near
the start:

```c
    ciel_init(argv[2], argv[4]);
```

Once you have built your program using libciel, you can run it by
creating a task that uses the `proc` executor, and specify a reference
to the binary using the `command` attribute.

For more information about CIEL, please visit:

http://www.cl.cam.ac.uk/netos/ciel/
