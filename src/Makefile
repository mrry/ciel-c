default: libciel.so

libciel.o: libciel.c
	gcc libciel.c -std=gnu99 -c -o libciel.o -D_FILE_OFFSET_BITS=64 -fPIC

libciel.so: libciel.o
	gcc libciel.o -o libciel.so -shared -Wl -ljansson

clean:
	-rm *.o *.so