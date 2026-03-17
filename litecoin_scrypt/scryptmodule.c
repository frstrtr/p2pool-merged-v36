#include <Python.h>
#include "scrypt.h"

/* Python 2.x / PyPy binding */
static PyObject *scrypt_getpowhash(PyObject *self, PyObject *args)
{
    char *output;
    PyObject *value;
    PyStringObject *input;
    (void)self;  /* required by Python C API convention */

    if (!PyArg_ParseTuple(args, "S", &input))
        return NULL;
    Py_INCREF(input);
    output = PyMem_Malloc(32);
    if (output == NULL) {
        Py_DECREF(input);
        return PyErr_NoMemory();
    }

    scrypt_1024_1_1_256((char *)PyString_AsString((PyObject*) input), output);
    Py_DECREF(input);
    value = Py_BuildValue("s#", output, 32);
    PyMem_Free(output);
    return value;
}

static PyMethodDef ScryptMethods[] = {
    { "getPoWHash", scrypt_getpowhash, METH_VARARGS, "Returns the proof of work hash using scrypt" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC initltc_scrypt(void);
PyMODINIT_FUNC initltc_scrypt(void) {
    (void) Py_InitModule("ltc_scrypt", ScryptMethods);
}
