// A small ALU: one combinational process with a case statement, and flags.

module alu (
    input  logic [1:0] op,
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y,
    output logic       zero,
    output logic       carry
);
  logic [8:0] wide;

  always_comb begin
    case (op)
      2'd0: wide = {1'b0, a} + {1'b0, b};
      2'd1: wide = {1'b0, a} - {1'b0, b};
      2'd2: wide = {1'b0, a & b};
      default: wide = {1'b0, a | b};
    endcase
  end

  assign y     = wide[7:0];
  assign carry = wide[8];
  assign zero  = y == 8'd0;
endmodule
