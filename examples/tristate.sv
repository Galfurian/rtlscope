// A bidirectional pad: an inout driven when enabled, read back always.

module pad (
    input  logic oe,
    input  logic out,
    output logic in,
    inout  wire  io
);
  assign io = oe ? out : 1'bz;
  assign in = io;
endmodule

module tristate (
    input  logic oe,
    input  logic tx,
    output logic rx,
    inout  wire  pin
);
  pad u_pad (.oe(oe), .out(tx), .in(rx), .io(pin));
endmodule
