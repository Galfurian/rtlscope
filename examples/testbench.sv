// A testbench: a clock generator, a stimulus block and the design under test.

module dut (
    input  logic       clk,
    input  logic       rst,
    output logic [3:0] count
);
  always_ff @(posedge clk) begin
    if (rst) count <= '0;
    else count <= count + 1'b1;
  end
endmodule

module testbench;
  logic       clk = 1'b0;
  logic       rst;
  logic [3:0] count;

  dut u_dut (.clk(clk), .rst(rst), .count(count));

  always #5 clk = ~clk;

  initial begin
    rst = 1'b1;
    #12 rst = 1'b0;
    #100 $finish;
  end
endmodule
