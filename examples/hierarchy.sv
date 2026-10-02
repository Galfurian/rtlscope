// Three levels: a system made of a core, itself made of an ALU and a register.

module acc_alu (
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y
);
  assign y = a + b;
endmodule

module acc_reg (
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] d,
    output logic [7:0] q
);
  always_ff @(posedge clk) begin
    if (rst) q <= '0;
    else q <= d;
  end
endmodule

module core (
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] operand,
    output logic [7:0] acc
);
  logic [7:0] sum;

  acc_alu u_alu (.a(acc), .b(operand), .y(sum));
  acc_reg u_reg (.clk(clk), .rst(rst), .d(sum), .q(acc));
endmodule

module hierarchy (
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] x,
    input  logic [7:0] y,
    output logic [7:0] acc_x,
    output logic [7:0] acc_y
);
  core u_core_x (.clk(clk), .rst(rst), .operand(x), .acc(acc_x));
  core u_core_y (.clk(clk), .rst(rst), .operand(y), .acc(acc_y));
endmodule
