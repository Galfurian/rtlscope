// A two-stage pipelined adder: registered operands, then a registered sum.

module register #(
    parameter int W = 8
) (
    input  logic         clk,
    input  logic         rst,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
  always_ff @(posedge clk) begin
    if (rst) q <= '0;
    else q <= d;
  end
endmodule

module adder #(
    parameter int W = 8
) (
    input  logic [W-1:0] a,
    input  logic [W-1:0] b,
    output logic [W-1:0] y
);
  assign y = a + b;
endmodule

module pipeline (
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y
);
  logic [7:0] a_q, b_q, sum;

  register u_a   (.clk(clk), .rst(rst), .d(a),   .q(a_q));
  register u_b   (.clk(clk), .rst(rst), .d(b),   .q(b_q));
  adder    u_add (.a(a_q),   .b(b_q),   .y(sum));
  register u_y   (.clk(clk), .rst(rst), .d(sum), .q(y));
endmodule
