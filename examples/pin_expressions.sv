// Pins connected to a slice, a concatenation and constants.

module sink8 (
    input  logic [7:0] d,
    input  logic       en,
    output logic [7:0] q
);
  assign q = en ? d : 8'd0;
endmodule

module pin_expressions (
    input  logic [7:0] bus,
    input  logic [3:0] lo,
    input  logic [3:0] hi,
    output logic [7:0] q_slice,
    output logic [7:0] q_concat,
    output logic [7:0] q_const
);
  sink8 u_slice  (.d({4'd0, bus[7:4]}), .en(1'b1), .q(q_slice));
  sink8 u_concat (.d({hi, lo}),         .en(bus[0]), .q(q_concat));
  sink8 u_const  (.d(8'hA5),            .en(1'b1), .q(q_const));
endmodule
